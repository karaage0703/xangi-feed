import hashlib
import json
import math
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .feeds import web_url, topics, TOPICS
from .adapters import validate as validate_adapter
from .youtube import resolve as youtube_feed
from .catalog import export as catalog_sources
from .publication import label as publication_label, clean, GENERIC_NAMES

SOURCE_KINDS = {"rss", "tech", "blog", "news", "comic", "podcast", "paper", "video"}
DISCOVERY_DEFAULTS = {'discovery_enabled': True, 'discovery_interval_hours': 24, 'discovery_prompt': ''}
RECOMMENDATION_DEFAULTS = {
    'recommendation_min_preference': None,
    'recommendation_days': 0,
    'recommendation_hide_bad': False,
    'recommendation_mix_types': True,
    'recommendation_priority': 'balanced',
}

PRIORITIES = {
    'balanced': (1, .7),
    'fresh': (2, .35),
    'interest': (.5, 1.4),
}


def canonical(url):
    parts = urlsplit(web_url(url))
    host = (parts.hostname or "").lower()
    # arXiv abstract/PDF URLs and revisions identify one paper.
    if host in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        import re
        match = re.fullmatch(r"/(?:abs|pdf)/(.+?)(?:v\d+)?(?:\.pdf)?", parts.path)
        if match:
            return "https://arxiv.org/abs/" + match[1]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(sorted(query)), ""))


def tag_name(name):
    if not isinstance(name, str) or any(ord(c) < 32 for c in name):
        raise ValueError('タグ名は1〜40文字で指定してください')
    name = unicodedata.normalize('NFC', name.strip())
    if not 1 <= len(name) <= 40:
        raise ValueError('タグ名は1〜40文字で指定してください')
    return name


class Store:
    def __init__(self, path, seed=True, catalog_path=None):
        self.path = Path(path)
        self.catalog_path = catalog_path
        self.catalog_enabled = seed
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                checked REAL, succeeded REAL, error TEXT, count INTEGER NOT NULL DEFAULT 0,
                adapter TEXT NOT NULL DEFAULT 'rss', deleted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
                summary TEXT NOT NULL, published REAL, updated REAL, fetched REAL NOT NULL,
                audio_url TEXT, kind TEXT NOT NULL, topics TEXT NOT NULL,
                rating INTEGER NOT NULL DEFAULT 0, saved INTEGER NOT NULL DEFAULT 0,
                read INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
                session_id TEXT, discussion_state TEXT NOT NULL DEFAULT 'idle', discussion_error TEXT,
                discussion_agent_id TEXT, content_origin TEXT NOT NULL DEFAULT 'rss',
                note TEXT NOT NULL DEFAULT '', note_updated REAL, read_at REAL, manual_added_at REAL,
                skipped INTEGER NOT NULL DEFAULT 0, manually_added INTEGER NOT NULL DEFAULT 0,
                content_text TEXT NOT NULL DEFAULT '', content_kind TEXT NOT NULL DEFAULT '',
                fetch_error TEXT NOT NULL DEFAULT '', fetch_state TEXT NOT NULL DEFAULT '',
                publication_name TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS item_sources (
                item_id TEXT NOT NULL REFERENCES items(id),
                source_id INTEGER NOT NULL REFERENCES sources(id),
                PRIMARY KEY (item_id, source_id));
            CREATE TABLE IF NOT EXISTS manual_tags (name TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS item_tags (
                item_id TEXT NOT NULL REFERENCES items(id),
                tag_name TEXT NOT NULL REFERENCES manual_tags(name) ON DELETE CASCADE,
                PRIMARY KEY (item_id, tag_name));
            CREATE TABLE IF NOT EXISTS analyses (
                item_id TEXT PRIMARY KEY REFERENCES items(id), input_hash TEXT NOT NULL,
                status TEXT NOT NULL, result TEXT, error TEXT, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS discussion_requests (
                id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id),
                fingerprint TEXT NOT NULL, state TEXT NOT NULL,
                result TEXT, created REAL NOT NULL);
        """)
        self.db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("UPDATE items SET fetch_state='error',fetch_error='本文取得中に再起動しました。本文を再取得してください。' WHERE fetch_state='fetching'")
        self.db.execute('CREATE TABLE IF NOT EXISTS save_requests (id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id), payload TEXT NOT NULL, created REAL NOT NULL)')
        self.db.execute('CREATE INDEX IF NOT EXISTS save_requests_item ON save_requests(item_id, created)')
        for pending in self.db.execute('SELECT id,payload FROM save_requests').fetchall():
            state = json.loads(pending['payload'])
            if state.get('status') in {'creating', 'sending'}:
                state.update(status='uncertain', error='保存依頼の送信中に再起動しました。会話を確認してください。自動再送はしません')
                self.db.execute('UPDATE save_requests SET payload=? WHERE id=?', (json.dumps(state, ensure_ascii=False), pending['id']))
        if seed:
            self.sync_catalog()
        with self.db:
            self.db.execute("UPDATE items SET discussion_state='uncertain', discussion_error='処理中に再起動しました。会話を確認してください。自動再送はしません。' WHERE discussion_state IN ('creating','sending')")
            self.db.execute("UPDATE analyses SET status='error',error='処理中に再起動しました。再生成してください' WHERE status='processing'")
            self.db.execute("UPDATE discussion_requests SET state='uncertain' WHERE state='pending'")

    @contextmanager
    def transaction(self):
        with self.lock, self.db:
            yield self.db

    def sync_catalog(self):
        definitions = catalog_sources('feed', self.catalog_path)
        digest = hashlib.sha256(json.dumps(definitions, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            stored = self.db.execute("SELECT value FROM metadata WHERE key='shared-catalog-v1'").fetchone()
            if stored and stored[0] == digest:
                return
            managed = self.db.execute("SELECT value FROM metadata WHERE key='shared-catalog-urls'").fetchone()
            old_urls = set(json.loads(managed[0])) if managed else set()
            new_urls = {web_url(s['url']) for s in definitions}
            with self.transaction() as db:
                for source in definitions:
                    db.execute('INSERT INTO sources(name,url,kind,adapter) VALUES (?,?,?,?) '
                               'ON CONFLICT(url) DO UPDATE SET name=excluded.name,kind=excluded.kind,adapter=excluded.adapter',
                               (source['name'], web_url(source['url']), source['kind'], source['adapter']))
                for url in old_urls - new_urls:
                    db.execute('UPDATE sources SET enabled=0 WHERE url=?', (url,))
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('shared-catalog-v1',?)", (digest,))
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('shared-catalog-urls',?)", (json.dumps(sorted(new_urls)),))

    def sources(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM sources WHERE deleted=0 ORDER BY id")]

    def source(self, source_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM sources WHERE id=? AND deleted=0", (source_id,)).fetchone()
            if row is None:
                raise KeyError("配信元が見つかりません")
            return dict(row)

    def add_source(self, name, url, kind, adapter='rss'):
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("配信元の名前は1〜100文字で指定してください")
        if kind not in SOURCE_KINDS:
            raise ValueError("未対応の配信元種別です")
        if kind == 'video':
            if adapter != 'rss':
                raise ValueError('YouTubeはRSS取得を選んでください')
            url = youtube_feed(url)
        url = web_url(url)
        validate_adapter(adapter, url)
        with self.transaction() as db:
            removed = db.execute('SELECT id FROM sources WHERE url=? AND deleted=1', (url,)).fetchone()
            if removed:
                db.execute('UPDATE sources SET name=?,kind=?,adapter=?,enabled=1,deleted=0,error=NULL WHERE id=?',
                           (name.strip(), kind, adapter, removed['id']))
                return removed['id']
            cursor = db.execute("INSERT INTO sources(name,url,kind,adapter) VALUES (?,?,?,?)", (name.strip(), url, kind, adapter))
            return cursor.lastrowid

    def add_article(self, data):
        if data.keys() - {'url', 'title', 'summary'}:
            raise ValueError('記事にはurl・title・summaryだけを指定してください')
        for key, limit in [('url', 4000), ('title', 1000), ('summary', 5000)]:
            value = data.get(key, '')
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError(f'{key}は{limit}文字以内の文字列で指定してください')
        url = canonical(data.get('url', '').strip())
        item_id = hashlib.sha256(url.encode()).hexdigest()[:24]
        title = data.get('title', '').strip() or url
        summary = data.get('summary', '').strip()
        with self.transaction() as db:
            # The same canonical identity as RSS; never overwrite existing user data.
            exists = db.execute('SELECT id FROM items WHERE id=?', (item_id,)).fetchone()
            if not exists:
                db.execute("""INSERT INTO items(id,url,title,summary,fetched,kind,topics,content_origin)
                           VALUES (?,?,?,?,?,'rss',?,'manual')""",
                           (item_id, url, title, summary, time.time(), json.dumps(topics(title + ' ' + summary))))
            db.execute('UPDATE items SET manually_added=1,manual_added_at=COALESCE(manual_added_at,?) WHERE id=?', (time.time(), item_id))
            return self.item(item_id), not bool(exists)

    def article_fetch_state(self, item_id, state):
        with self.transaction() as db:
            item = self.item(item_id)
            if item['content_origin'] != 'manual':
                raise ValueError('直接追加した記事だけ本文を取得できます')
            db.execute('UPDATE items SET fetch_state=? WHERE id=?', (state, item_id))

    def save_article_content(self, item_id, fields):
        # Read inside the transaction so a concurrent memo/rating/edit is kept.
        with self.transaction() as db:
            item = self.item(item_id)
            if item['content_origin'] != 'manual':
                raise ValueError('直接追加した記事だけ本文を更新できます')
            title = fields.get('title') if item['title'] == item['url'] else item['title']
            title = title or item['title']
            summary = item['summary'] or fields.get('summary', '')
            body = fields.get('body') or item.get('content_text', '')
            db.execute('UPDATE items SET title=?,summary=?,content_text=?,content_kind=?,fetch_error=?,fetch_state=?,kind=?,published=COALESCE(published,?),topics=? WHERE id=?',
                       (title, summary, body, fields.get('content_kind', item.get('content_kind', '')), fields.get('error', ''), 'error' if fields.get('error') else 'done', fields.get('kind', item['kind']), fields.get('published'),
                        json.dumps(topics(title + ' ' + summary), ensure_ascii=False), item_id))
            self.save_publication(db, item_id, fields)
        return self.item(item_id)

    def enable_source(self, source_id, enabled):
        self.source(source_id)
        with self.transaction() as db:
            db.execute("UPDATE sources SET enabled=? WHERE id=?", (int(enabled), source_id))

    def delete_source(self, source_id):
        # Retain attribution and user data; catalog sync must not resurrect this row.
        with self.transaction() as db:
            self.source(source_id)
            db.execute('UPDATE sources SET enabled=0,deleted=1 WHERE id=?', (source_id,))

    def ingest(self, source, entries):
        added = 0
        now = time.time()
        with self.transaction() as db:
            active = db.execute('SELECT deleted FROM sources WHERE id=?', (source['id'],)).fetchone()
            if active is None or active['deleted']:
                return 0
            for entry in entries:
                url = canonical(entry["url"])
                identity = entry.get('identity') if source.get('adapter', 'rss') != 'rss' else None
                key = json.dumps([source['adapter'], url, identity], ensure_ascii=False) if identity else url
                item_id = hashlib.sha256(key.encode()).hexdigest()[:24]
                origin = 'page' if source.get('adapter', 'rss') != 'rss' or (source['kind'] == 'video' and entry.get('origin') == 'page') else 'rss'
                exists = db.execute("SELECT published,updated FROM items WHERE id=?", (item_id,)).fetchone()
                if not exists:
                    db.execute("""INSERT INTO items(id,url,title,summary,published,updated,fetched,audio_url,kind,topics,content_origin)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                               (item_id, url, entry["title"], entry["summary"], entry["published"], entry.get("updated"),
                                now, entry.get("audio_url"), source["kind"], json.dumps(entry["topics"], ensure_ascii=False), origin))
                    added += 1
                else:
                    # Retain rating, first-seen time and conversation across revisions.
                    updated = entry.get('updated')
                    if updated is None and exists['published'] is not None and entry['published'] is not None and entry['published'] > exists['published']:
                        updated = entry['published']
                    known_updates = [v for v in [updated, exists['updated']] if v is not None]
                    updated = max(known_updates) if known_updates else None
                    db.execute("""UPDATE items SET title=?,summary=?,published=COALESCE(published,?),updated=?,audio_url=COALESCE(?,audio_url),topics=?,content_origin=? WHERE id=?""",
                               (entry["title"], entry["summary"], entry['published'], updated, entry.get("audio_url"),
                                json.dumps(entry["topics"], ensure_ascii=False), origin, item_id))
                    if source['kind'] != 'rss':
                        db.execute("UPDATE items SET kind=? WHERE id=? AND kind IN ('rss','tech')", (source['kind'], item_id))
                self.save_publication(db, item_id, entry)
                db.execute("INSERT OR IGNORE INTO item_sources VALUES (?,?)", (item_id, source["id"]))
            db.execute("UPDATE sources SET checked=?,succeeded=?,error=NULL,count=? WHERE id=?",
                       (now, now, len(entries), source["id"]))
        return added

    def source_failed(self, source_id, error):
        with self.transaction() as db:
            db.execute("UPDATE sources SET checked=?,error=? WHERE id=?", (time.time(), str(error)[:500], source_id))

    @staticmethod
    def save_publication(db, item_id, fields):
        publication = clean(fields.get('publication_name'))
        if publication.casefold() in GENERIC_NAMES:
            publication = ''
        db.execute("UPDATE items SET publication_name=COALESCE(NULLIF(?,''),publication_name),author=COALESCE(NULLIF(?,''),author) WHERE id=?",
                   (publication, clean(fields.get('author')), item_id))

    def preference(self, item, weights=None):
        weights = weights if weights is not None else {t['name']: t['weight'] for t in self.profile()['topics']}
        own = item['rating'] if item['reason'] not in {'duplicate', 'summary'} else 0
        score = sum(max(-3, min(3, weights.get(topic, 0))) for topic in item['topics']) + own
        return {'preference_score': score,
                'preference_known': own != 0 or any(topic in weights for topic in item['topics'])}

    def item(self, item_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise KeyError("記事が見つかりません")
            item = dict(row)
            item['muted'] = bool(item.pop('skipped'))
            item["topics"] = json.loads(item["topics"])
            item['tags'] = [r[0] for r in self.db.execute('SELECT tag_name FROM item_tags WHERE item_id=? ORDER BY tag_name', (item_id,))]
            item["sources"] = [dict(r) for r in self.db.execute(
                "SELECT s.id,s.name FROM sources s JOIN item_sources x ON x.source_id=s.id WHERE x.item_id=? ORDER BY s.id", (item_id,))]
            item['publisher_label'] = publication_label(item)
            item.update(self.preference(item))
            item['analysis'] = self.analysis(item_id)
            item['save_request'] = self.save_request(item_id)
            if item['analysis'] and item['analysis']['input_hash'] != self.content_hash(item):
                item['analysis'] = None
            return item

    def tag_options(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT t.name, COUNT(x.item_id) AS count FROM manual_tags t LEFT JOIN item_tags x ON x.tag_name=t.name GROUP BY t.name ORDER BY t.name')]

    def change_tag(self, item_id, operation, name):
        name = tag_name(name)
        if operation not in {'add', 'remove'}:
            raise ValueError('タグの追加または取り外しを指定してください')
        with self.transaction() as db:
            self.item(item_id)
            if operation == 'add':
                existing = [r[0] for r in db.execute('SELECT tag_name FROM item_tags WHERE item_id=?', (item_id,))]
                if name not in existing and len(existing) >= 20:
                    raise ValueError('1記事のタグは20個までです')
                db.execute('INSERT OR IGNORE INTO manual_tags VALUES (?)', (name,))
                db.execute('INSERT OR IGNORE INTO item_tags VALUES (?,?)', (item_id, name))
            else:
                db.execute('DELETE FROM item_tags WHERE item_id=? AND tag_name=?', (item_id, name))
            return self.item(item_id)

    def delete_tag(self, name):
        name = tag_name(name)
        with self.transaction() as db:
            deleted = db.execute('DELETE FROM manual_tags WHERE name=?', (name,)).rowcount
            if not deleted:
                raise KeyError('タグが見つかりません')

    def settings(self):
        with self.lock:
            row = self.db.execute("SELECT value FROM metadata WHERE key='settings'").fetchone()
        defaults = {'show_link_previews': True, 'enabled': True, 'agent_id': '', 'automatic_limit': 30, 'timeout_seconds': 300, 'mark_read_on_open': True, 'save_skill_name': 'xangi-feed-save', 'save_agent_id': '', **DISCOVERY_DEFAULTS, **RECOMMENDATION_DEFAULTS}
        stored = json.loads(row[0]) if row else {}
        settings = {key: stored.get(key, value) for key, value in defaults.items()}
        return settings

    def save_settings(self, data):
        settings = self.settings()
        for key in data:
            if key not in settings:
                raise ValueError('未対応の設定です')
        if 'recommendation_min_preference' in data and data['recommendation_min_preference'] is not None and (type(data['recommendation_min_preference']) is not int or not -100 <= data['recommendation_min_preference'] <= 100):
            raise ValueError('好みの下限は-100〜100の整数か、未指定にしてください')
        if 'discovery_prompt' in data and (not isinstance(data['discovery_prompt'], str) or len(data['discovery_prompt']) > 2000):
            raise ValueError('検索プロンプトは2000文字以内の文字列です')
        if 'discovery_enabled' in data and type(data['discovery_enabled']) is not bool:
            raise ValueError('discovery_enabledはbooleanです')
        if 'discovery_interval_hours' in data and (type(data['discovery_interval_hours']) is not int or not 6 <= data['discovery_interval_hours'] <= 168):
            raise ValueError('探索間隔は6〜168時間です')
        if 'show_link_previews' in data and type(data['show_link_previews']) is not bool:
            raise ValueError('show_link_previewsはbooleanです')
        if 'mark_read_on_open' in data and type(data['mark_read_on_open']) is not bool:
            raise ValueError('mark_read_on_openはbooleanです')
        if 'enabled' in data and type(data['enabled']) is not bool:
            raise ValueError('enabledはbooleanです')
        if 'automatic_limit' in data and (type(data['automatic_limit']) is not int or not 0 <= data['automatic_limit'] <= 100):
            raise ValueError('自動解説の件数は0〜100です')
        if 'timeout_seconds' in data and (type(data['timeout_seconds']) is not int or not 1 <= data['timeout_seconds'] <= 1800):
            raise ValueError('AIの待ち時間は1〜1800秒です')
        for key, maximum in [('recommendation_days', 365)]:
            if key in data and (type(data[key]) is not int or not 0 <= data[key] <= maximum):
                raise ValueError(f'{key}は0〜{maximum}の整数です')
        for key in ['recommendation_hide_bad', 'recommendation_mix_types']:
            if key in data and type(data[key]) is not bool:
                raise ValueError(f'{key}はbooleanです')
        if 'recommendation_priority' in data and (not isinstance(data['recommendation_priority'], str) or data['recommendation_priority'] not in PRIORITIES):
            raise ValueError('未対応のおすすめ優先順です')
        for key, maximum in [('agent_id', 200)]:
            if key in data and (not isinstance(data[key], str) or len(data[key]) > maximum):
                raise ValueError(f'{key}の値が不正です')
        settings.update(data)
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('settings',?)", (json.dumps(settings, ensure_ascii=False),))
        return self.settings()

    def save_request(self, item_id=None, request_id=None):
        with self.lock:
            if request_id is not None:
                row = self.db.execute('SELECT payload FROM save_requests WHERE id=?', (request_id,)).fetchone()
            elif item_id is not None:
                row = self.db.execute('SELECT payload FROM save_requests WHERE item_id=? ORDER BY created DESC LIMIT 1', (item_id,)).fetchone()
            else:
                return None
        return json.loads(row[0]) if row else None

    def record_save_request(self, state, pending_only=False):
        with self.transaction() as db:
            previous = self.save_request(request_id=state['request_id'])
            if previous and previous['item_id'] != state['item_id']:
                raise ValueError('保存依頼の記事が一致しません')
            if pending_only and previous and previous['status'] not in {'creating', 'sending'}:
                return
            db.execute('INSERT INTO save_requests VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                       (state['request_id'], state['item_id'], json.dumps(state, ensure_ascii=False), state['created']))

    def save_note(self, item_id, note):
        if not isinstance(note, str) or len(note) > 4000:
            raise ValueError('メモは4000文字までです')
        self.item(item_id)
        with self.transaction() as db:
            db.execute('UPDATE items SET note=?,note_updated=? WHERE id=?', (note, time.time(), item_id))
        return self.item(item_id)

    def analysis(self, item_id):
        with self.lock:
            row = self.db.execute('SELECT * FROM analyses WHERE item_id=?', (item_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['result'] = json.loads(result['result']) if result['result'] else None
        return result

    def save_analysis(self, item_id, input_hash, status, result=None, error=None):
        with self.transaction() as db:
            if result is None and status != 'done':
                previous = self.analysis(item_id)
                if previous and previous['input_hash'] == input_hash:
                    result = previous['result']
            db.execute('INSERT OR REPLACE INTO analyses VALUES (?,?,?,?,?,?)',
                       (item_id, input_hash, status, json.dumps(result, ensure_ascii=False) if result else None, error, time.time()))

    def content_hash(self, item):
        content = ['summary-v2', item['title'], item['summary']]
        if item.get('content_text'):
            content.append(item['content_text'])
        if item.get('content_origin', 'rss') != 'rss':
            content.append(item['content_origin'])
        return hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()

    def open_item(self, item_id):
        # Consult the persisted policy on every click, including from stale tabs.
        with self.transaction() as db:
            item = self.item(item_id)
            marked = bool(self.settings()['mark_read_on_open'] and not item['read'])
            if marked:
                db.execute('UPDATE items SET read=1,read_at=? WHERE id=?', (time.time(), item_id))
            return {'item': self.item(item_id), 'marked_read': marked}

    def react(self, item_id, data):
        self.item(item_id)
        if data.keys() - {'rating', 'saved', 'read', 'reason', 'muted'}:
            raise ValueError('未対応の評価項目です')
        values = {}
        for field in {"rating", "saved", "read", "reason", "muted"} & data.keys():
            value = data[field]
            if field == "rating" and (type(value) is not int or value not in {-1, 0, 1}):
                raise ValueError("ratingは-1 / 0 / 1です")
            if field in {"saved", "read", "muted"} and type(value) is not bool:
                raise ValueError(f"{field}はbooleanです")
            if field == "reason" and value not in {"", "interest", "duplicate", "summary"}:
                raise ValueError("未対応の評価理由です")
            values['skipped' if field == 'muted' else field] = value
        if not values:
            raise ValueError("更新する評価がありません")
        with self.transaction() as db:
            if 'read' in values:
                current = self.item(item_id)
                if not values['read']:
                    values['read_at'] = None
                elif not current['read']:
                    values['read_at'] = time.time()
            db.execute("UPDATE items SET " + ",".join(f"{k}=?" for k in values) + " WHERE id=?", (*values.values(), item_id))
            if "rating" in values and values["rating"] != -1:
                db.execute("UPDATE items SET reason='' WHERE id=?", (item_id,))
        item = self.item(item_id)
        if {'rating', 'reason'} & values.keys():
            item['preference_weights'] = self.profile()['topics']
        return item

    def discovery_state(self):
        with self.lock:
            row = self.db.execute("SELECT value FROM metadata WHERE key='discovery'").fetchone()
        return json.loads(row[0]) if row else {'items': [], 'updated': None, 'last_attempt': None}

    def save_discovery(self, value):
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('discovery',?)", (json.dumps(value, ensure_ascii=False),))

    def profile(self):
        weights = {}
        with self.lock:
            rows = list(self.db.execute("SELECT rating,reason,topics FROM items WHERE rating!=0"))
            counts = dict(self.db.execute("SELECT rating,COUNT(*) FROM items GROUP BY rating").fetchall())
            saved = self.db.execute("SELECT COUNT(*) FROM items WHERE saved=1").fetchone()[0]
        for row in rows:
            if row["rating"] == -1 and row["reason"] in {"duplicate", "summary"}:
                continue
            for topic in json.loads(row["topics"]):
                weights[topic] = weights.get(topic, 0) + row["rating"]
        return {"good": counts.get(1, 0), "bad": counts.get(-1, 0), "saved": saved,
                "topics": [{"name": name, "weight": weight} for name, weight in sorted(weights.items(), key=lambda p: -p[1])]}

    def reset_profile(self):
        with self.transaction() as db:
            db.execute("UPDATE items SET rating=0,reason=''")

    def list_items(self, tab="recommended", kind="", q="", source_id="", limit=100, offset=0, apply_recommendation=True, sort=None, topics=(), tags=(), kinds=()):
        if tab not in {"recommended", "all", "saved", "notes", "discussed", "read", "manual", "muted"}:
            raise ValueError("未対応の表示です")
        if sort is None:
            sort = 'read_at' if tab == 'read' else 'manual_added_at' if tab == 'manual' else 'updated'
        if sort not in {'updated', 'published', 'preference', 'recommended', 'read_at', 'manual_added_at'}:
            raise ValueError('未対応の並び順です')
        if not isinstance(topics, (list, tuple)) or len(topics) > len(TOPICS) or any(not isinstance(t, str) or t not in TOPICS for t in topics):
            raise ValueError('既存の話題タグを選んでください')
        selected_topics = set(topics)
        if not isinstance(tags, (list, tuple)) or len(tags) > 20:
            raise ValueError('タグは20個以内で指定してください')
        selected_tags = {tag_name(name) for name in tags}
        with self.lock:
            save_requests = {r['item_id']: r['payload'] for r in self.db.execute('SELECT item_id,payload FROM save_requests ORDER BY created')}
            rows = [dict(r) for r in self.db.execute("SELECT * FROM items")]
            analyses = {r['item_id']: dict(r) for r in self.db.execute('SELECT * FROM analyses')}
            item_tags = {}
            for r in self.db.execute('SELECT item_id,tag_name FROM item_tags ORDER BY tag_name'):
                item_tags.setdefault(r['item_id'], []).append(r['tag_name'])
            sources = {}
            for r in self.db.execute("SELECT x.item_id,s.id,s.name FROM item_sources x JOIN sources s ON s.id=x.source_id"):
                sources.setdefault(r["item_id"], []).append({"id": r["id"], "name": r["name"]})
        profile = self.profile()["topics"]
        weights = {t["name"]: t["weight"] for t in profile}
        settings = self.settings() if apply_recommendation else RECOMMENDATION_DEFAULTS
        freshness_weight, preference_weight = PRIORITIES[settings['recommendation_priority']]
        now = time.time()
        result = []
        def date_value(item, keys):
            return next((item[k] for k in keys if item[k] is not None), 0)
        def item_analysis(item):
            analysis = analyses.get(item['id'])
            if analysis:
                analysis = dict(analysis)
                analysis['result'] = json.loads(analysis['result']) if analysis['result'] else None
                if analysis['input_hash'] != self.content_hash(item):
                    return None
            return analysis
        for item in rows:
            item['muted'] = bool(item.pop('skipped'))
            if item['muted'] != (tab == 'muted'):
                continue
            if tab == 'manual' and not item['manually_added']:
                continue
            item['tags'] = item_tags.get(item['id'], [])
            if selected_tags and not selected_tags.intersection(item['tags']):
                continue
            if tab == 'recommended':
                if apply_recommendation and (item['read'] or (not sources.get(item['id']) and item['content_origin'] != 'manual')):
                    continue
                if settings['recommendation_days'] and date_value(item, ['updated', 'published', 'fetched']) < now - settings['recommendation_days'] * 86400:
                    continue
                if settings['recommendation_hide_bad'] and item['rating'] == -1:
                    continue
            if tab == "saved" and not item["saved"]:
                continue
            if tab == 'notes' and not item['note'].strip():
                continue
            if tab == 'read' and not item['read']:
                continue
            if tab == "discussed" and not item["session_id"]:
                continue
            if (kind or kinds) and item["kind"] not in ({kind} | set(kinds)):
                continue
            item["sources"] = sources.get(item["id"], [])
            if source_id and not any(str(s["id"]) == source_id for s in item["sources"]):
                continue
            # Search still includes current analysis results before pagination.
            if q:
                item['analysis'] = item_analysis(item)
                analysis_result = (item['analysis'] or {}).get('result') or {}
                haystack = (item['title'] + ' ' + item['summary'] + ' ' + item['note'] + ' ' + ' '.join(item['tags']) + json.dumps(analysis_result, ensure_ascii=False)).casefold()
                if not all(term in haystack for term in q.casefold().split()):
                    continue
            item["topics"] = json.loads(item["topics"])
            if selected_topics and not selected_topics.intersection(item['topics']):
                continue
            matches = [t for t in item["topics"] if weights.get(t, 0) > 0]
            age = max(0, now - (item["published"] or item["fetched"])) / 3600
            preference = sum(max(-3, min(3, weights.get(t, 0))) for t in item["topics"])
            own_rating = item['rating'] if item['reason'] not in {'duplicate', 'summary'} else 0
            item.update(self.preference(item, weights))
            minimum = settings['recommendation_min_preference']
            if tab == 'recommended' and minimum is not None and (not item['preference_known'] or item['preference_score'] < minimum):
                continue
            item["score"] = freshness_weight / (1 + age / 48) + math.tanh(preference / 3) * preference_weight - (0.6 if own_rating == -1 else 0)
            item["recommendation"] = "好み（Good／Bad）: " + "、".join(matches) if matches else "好み（Good／Bad）: 一致するGoodの話題はまだありません"
            result.append(item)
        def order(item):
            updated = date_value(item, ['updated', 'published', 'fetched'])
            primary = updated
            if sort == 'published':
                primary = date_value(item, ['published', 'fetched'])
            elif sort == 'preference':
                primary = item['preference_score']
            elif sort == 'recommended':
                primary = item['score']
            elif sort == 'manual_added_at':
                return (item['manual_added_at'] if item['manual_added_at'] is not None else item['fetched']), item['id']
            elif sort == 'read_at':
                return item['read_at'] is not None, item['read_at'] or 0, updated, item['fetched'], item['id']
            return primary, updated, item['fetched'], item['id']
        result.sort(key=order, reverse=True)
        if tab == 'recommended' and sort == 'recommended' and settings['recommendation_mix_types'] and not kind and not kinds and not source_id:
            # Balance categories before slicing: an arXiv batch must not occupy
            # every visible row. New tab remains a strict chronological view.
            groups = {k: [] for k in ['tech', 'blog', 'podcast', 'paper', 'video', 'news', 'comic', 'rss']}
            for item in result:
                groups.setdefault(item['kind'], []).append(item)
            result = []
            while any(groups.values()):
                for group in groups.values():
                    if group:
                        result.append(group.pop(0))
        page = result[offset:offset + limit]
        for item in page:
            item['publisher_label'] = publication_label(item)
            if not q:
                item['analysis'] = item_analysis(item)
            payload = save_requests.get(item['id'])
            item['save_request'] = json.loads(payload) if payload is not None else None
        return {"items": page, "total": len(result)}

    def discussion(self, item_id, state, session_id=None, error=None, agent_id=None):
        with self.transaction() as db:
            db.execute("UPDATE items SET discussion_state=?,session_id=COALESCE(?,session_id),discussion_error=?,discussion_agent_id=COALESCE(?,discussion_agent_id) WHERE id=?",
                       (state, session_id, error, agent_id, item_id))

    def discussion_request(self, request_id, fingerprint):
        with self.lock:
            row = self.db.execute('SELECT * FROM discussion_requests WHERE id=?', (request_id,)).fetchone()
        if row is None:
            return None
        if row['fingerprint'] != fingerprint:
            raise ValueError('同じリクエストIDで内容を変更できません')
        if row['result']:
            return json.loads(row['result'])
        raise ValueError('前回の送信結果が不明か処理中です。会話一覧を確認してください。自動再送はしません')

    def begin_discussion_request(self, request_id, item_id, fingerprint):
        with self.transaction() as db:
            db.execute('INSERT INTO discussion_requests VALUES (?,?,?,?,?,?)', (request_id, item_id, fingerprint, 'pending', None, time.time()))

    def finish_discussion_request(self, request_id, state, result=None):
        with self.transaction() as db:
            db.execute('UPDATE discussion_requests SET state=?,result=? WHERE id=?',
                       (state, json.dumps(result, ensure_ascii=False) if result else None, request_id))

    def close(self):
        with self.lock:
            self.db.close()
