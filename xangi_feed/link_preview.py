"""Bounded, cached public Open Graph cards; remote markup is never rendered."""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

from .adapters.html import tree
from .feeds import _retrieve, web_url, plain

HTML_LIMIT = 2 * 1024 * 1024
IMAGE_LIMIT = 1024 * 1024


def fetch_public(url, maximum):
    return _retrieve(url, maximum, timeout=8, deadline_seconds=20, head_only=maximum == HTML_LIMIT)


def metadata(data, url):
    charset = re.search(br'charset\s*=\s*["\']?([\w-]+)', data[:4096], re.I)
    root = tree(data, charset[1].decode('ascii') if charset else 'utf-8-sig')
    values = {}
    for node in root.find('meta'):
        key = node.attrs.get('property', node.attrs.get('name', '')).lower()
        values.setdefault(key, node.attrs.get('content', ''))
    image = values.get('og:image') or values.get('og:image:url') or values.get('twitter:image') or values.get('twitter:image:src')
    try:
        image = web_url(urljoin(url, image)) if image else ''
    except ValueError:
        image = ''
    return {'title': plain(values.get('og:title') or values.get('twitter:title') or next((n.text() for n in root.find('title')), ''), 300),
            'site_name': plain(values.get('og:site_name') or urlsplit(url).hostname, 100),
            'description': plain(values.get('og:description') or values.get('twitter:description') or values.get('description'), 280),
            'image_url': image}


def image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png'
    if data.startswith(b'\xff\xd8\xff'): return 'image/jpeg'
    if data.startswith((b'GIF87a', b'GIF89a')): return 'image/gif'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP': return 'image/webp'
    return ''


class LinkPreviews:
    def __init__(self, store, fetcher=None):
        self.store, self.fetcher = store, fetcher or fetch_public
        self.lock = threading.Lock()
        self.pending = set()
        self.stop = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='feed-preview')
        with store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS link_previews (item_id TEXT PRIMARY KEY, url TEXT, status TEXT, metadata TEXT, image BLOB, mime TEXT, updated REAL)')

    def _item(self, item_id):
        with self.store.lock:
            row = self.store.db.execute('SELECT id,url,title FROM items WHERE id=?', (item_id,)).fetchone()
        if row is None: raise KeyError('記事が見つかりません')
        return dict(row)

    @staticmethod
    def _fresh(row, url, now):
        return row['url'] == url and now - row['updated'] < (86400 if row['status'] == 'done' else 3600)

    def cached_for(self, items):
        if not items or not self.store.settings()['show_link_previews']: return {}
        urls = {item['id']: item['url'] for item in items}
        placeholders = ','.join('?' for _ in urls)
        with self.store.lock:
            rows = self.store.db.execute(f'SELECT item_id,url,status,metadata,updated FROM link_previews WHERE item_id IN ({placeholders})', tuple(urls)).fetchall()
        now = time.time()
        return {r['item_id']: {'status': r['status'], **json.loads(r['metadata'])}
                for r in rows if self._fresh(r, urls[r['item_id']], now)}

    def request(self, item_id):
        item = self._item(item_id)
        if not self.store.settings()['show_link_previews']:
            return {'status': 'disabled'}
        with self.store.lock:
            row = self.store.db.execute('SELECT url,status,metadata,updated FROM link_previews WHERE item_id=?', (item_id,)).fetchone()
        if row and self._fresh(row, item['url'], time.time()):
            return {'status': row['status'], **json.loads(row['metadata'])}
        with self.lock:
            if self.stop.is_set(): return {'status': 'disabled'}
            if item_id not in self.pending:
                if len(self.pending) >= 50: return {'status': 'busy'}
                self.pending.add(item_id)
                self.pool.submit(self._fetch, item)
        return {'status': 'pending'}

    def _fetch(self, item):
        image, mime = b'', ''
        result = {'title': item['title'], 'site_name': urlsplit(item['url']).hostname, 'description': '', 'has_image': False}
        status = 'error'
        try:
            if self.stop.is_set() or not self.store.settings()['show_link_previews']: return
            parsed = metadata(self.fetcher(item['url'], HTML_LIMIT), item['url'])
            image_url = parsed.pop('image_url')
            result.update({k: v for k, v in parsed.items() if v})
            status = 'done'
            if image_url and not self.stop.is_set() and self.store.settings()['show_link_previews']:
                try:
                    image = self.fetcher(image_url, IMAGE_LIMIT)
                    if len(image) > IMAGE_LIMIT: raise ValueError('image too large')
                    mime = image_type(image)
                    if not mime: image = b''
                except Exception:
                    image, mime = b'', ''
            result['has_image'] = bool(image)
        except Exception:
            pass
        finally:
            try:
                if not self.stop.is_set() and self.store.settings()['show_link_previews']:
                    with self.store.transaction() as db:
                        db.execute('INSERT OR REPLACE INTO link_previews VALUES (?,?,?,?,?,?,?)',
                                   (item['id'], item['url'], status, json.dumps(result, ensure_ascii=False), image, mime, time.time()))
                        db.execute('DELETE FROM link_previews WHERE item_id NOT IN (SELECT item_id FROM link_previews ORDER BY updated DESC LIMIT 256)')
            finally:
                with self.lock:
                    self.pending.discard(item['id'])

    def image(self, item_id):
        self._item(item_id)
        if not self.store.settings()['show_link_previews']: raise KeyError('リンクカードは非表示です')
        with self.store.lock:
            row = self.store.db.execute('SELECT image,mime FROM link_previews WHERE item_id=?', (item_id,)).fetchone()
        if not row or not row['image']: raise KeyError('プレビュー画像がありません')
        return row['image'], row['mime']

    def close(self):
        self.stop.set()
        self.pool.shutdown(wait=True, cancel_futures=True)
