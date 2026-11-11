import argparse
import base64
import hashlib
import hmac
import json
import os
import signal
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .service import Collector, Discussions, HostClient
from .store import Store, RECOMMENDATION_DEFAULTS
from .feeds import TOPICS
from .enrichment import Enricher
from .adapters import options as adapter_options
from .skill_saves import SkillSaves
from .article import ArticleImporter
from .recommendation import search_evidence
from .discovery import Discovery
from .link_preview import LinkPreviews

PUBLIC = Path(__file__).resolve().parent.parent / "public"
# The host's /ui permits this fixed inline handoff and injects the service base.
# Hash contents stay data; neither a URL nor an action is evaluated from them.
ENTRY_SCRIPT = """(function(){var capture=location.hash.indexOf('#url=')===0;var target=new URL(capture?'capture':'./',document.baseURI);if(capture)target.hash=location.hash;location.replace(target.href);})()"""
ENTRY_SCRIPT_HASH = base64.b64encode(hashlib.sha256(ENTRY_SCRIPT.encode()).digest()).decode()
ENTRY_HTML = ('<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>xangi-feed</title>'
              '<script>' + ENTRY_SCRIPT + '</script></head><body><noscript>'
              '<meta http-equiv="refresh" content="0;url=./"></noscript>'
              '<a href="./">Open xangi-feed</a></body></html>')



class Server(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, store, token, host=None, collector=None):
        self.store, self.token = store, token
        self.host = host or HostClient()
        self.collector = collector or Collector(store)
        self.discovery = Discovery(store, self.host)
        self.discussions = Discussions(store, self.host)
        self.enricher = Enricher(store, self.host)
        self.saving = SkillSaves(store, self.host)
        self.previews = LinkPreviews(store)
        self.articles = ArticleImporter(store)
        self.articles.on_complete = lambda item: self.enricher.enqueue([item], retry=True)
        self.collector.on_complete = self.enricher.automatic
        super().__init__(("127.0.0.1", 0), Handler)

    def server_close(self):
        self.previews.close()
        self.discovery.close()
        self.articles.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = "xangi-feed/0.1"

    def log_message(self, *_args):
        pass

    def send(self, status, data, content_type="application/json; charset=utf-8", script_hash=None):
        raw = json.dumps(data, ensure_ascii=False).encode() if content_type.startswith("application/json") else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        script_policy = "script-src 'self'" + (" 'sha256-" + script_hash + "'" if script_hash else "")
        self.send_header("Content-Security-Policy", "default-src 'self'; " + script_policy + "; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.handle_request(False)

    def do_POST(self):
        self.handle_request(True)

    def handle_request(self, mutation):
        expected = "Bearer " + self.server.token
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected.encode()):
            self.send(401, {"error": "認証が必要です"})
            return
        if mutation and self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send(403, {"error": "異なるサイトからの操作は拒否しました"})
            return
        path = urlsplit(self.path).path.rstrip("/") or "/"
        query = parse_qs(urlsplit(self.path).query)
        try:
            if mutation:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    self.send(415, {"error": "application/jsonが必要です"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    self.send(413, {"error": "リクエストサイズが不正です"})
                    return
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("JSON objectが必要です")
            else:
                data = {}
            store = self.server.store
            if not mutation and path == "/entry":
                self.send(200, ENTRY_HTML.encode(), "text/html; charset=utf-8", script_hash=ENTRY_SCRIPT_HASH)
            elif not mutation and path in {"/", "/ui", "/app.js", "/style.css", "/capture", "/capture.js", "/capture-core.js", "/capture.css", "/bookmarklet-settings.js"}:
                name = {"/": "index.html", "/ui": "index.html", "/capture": "capture.html"}.get(path, path[1:])
                mime = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8", "capture.html": "text/html; charset=utf-8", "capture.js": "text/javascript; charset=utf-8", "capture-core.js": "text/javascript; charset=utf-8", "capture.css": "text/css; charset=utf-8", "bookmarklet-settings.js": "text/javascript; charset=utf-8"}[name]
                self.send(200, (PUBLIC / name).read_bytes(), mime)
            elif not mutation and path == "/api/health":
                self.send(200, {"id": "xangi-feed", "version": "0.1.0", "ready": True,
                                "show_link_previews": store.settings()["show_link_previews"],
                                "capabilities": ["xangi-feed.manage"], "collector": self.server.collector.status(),
                                "enrichment": self.server.enricher.status(),
                                "discussion_available": self.server.host.configured, "save_revision": self.server.saving.revision})
            elif not mutation and path == '/api/discovery':
                self.send(200, self.server.discovery.status())
            elif mutation and path == '/api/discovery/refresh':
                started = self.server.discovery.request_refresh()
                self.send(202 if started else 200, self.server.discovery.status())
            elif not mutation and path == "/api/items":
                one = lambda key, default="": query.get(key, [default])[0]
                limit = max(1, min(100, int(one("limit", "50"))))
                offset = max(0, int(one("offset", "0")))
                result = store.list_items(one("tab", "recommended"), one("kind"), one("q")[:200], one("source"), limit, offset, sort=one('sort') or None, topics=query.get('topic', []), tags=query.get('tag', []), kinds=query.get('kind', []))
                cached = self.server.previews.cached_for(result['items'])
                for item in result['items']:
                    if item['id'] in cached: item['link_preview'] = cached[item['id']]
                self.server.enricher.enqueue(result['items'][:50])
                self.send(200, result)
            elif not mutation and path == '/api/recommendations':
                settings = store.settings()
                self.send(200, {key: settings[key] for key in RECOMMENDATION_DEFAULTS})
            elif not mutation and path == '/api/filter-options':
                self.send(200, {'topics': list(TOPICS), 'tags': store.tag_options()})
            elif mutation and path == '/api/tags/delete':
                if set(data) != {'name', 'confirm'} or data.get('confirm') is not True:
                    raise ValueError('タグをすべての記事から削除する確認が必要です')
                store.delete_tag(data['name'])
                self.send(200, {'deleted': True})
            elif not mutation and path == '/api/settings':
                try:
                    agents = self.server.host.agents() if self.server.host.configured else []
                    agent_error = None
                except Exception:
                    agents, agent_error = [], 'AIの候補を取得できません。現在の担当のまま停止・設定保存はできます。'
                self.send(200, {**store.settings(), 'save_mode': 'skill', 'save_available': self.server.host.configured, 'agent_error': agent_error, 'agents': [
                    {'id': a['id'], 'name': a['name'], 'backend': a.get('backend', ''),
                     'model': a.get('model', ''), 'available': a.get('available', True)} for a in agents]})
            elif not mutation and path == '/api/discussion-options':
                agents = self.server.host.agents() if self.server.host.configured else []
                self.send(200, {'default_agent_id': self.server.host.agent_id, 'project_id': None, 'agents': [
                    {'id': a['id'], 'name': a['name'], 'model': a.get('model', ''), 'available': a.get('available', True)} for a in agents]})
            elif mutation and path == '/api/settings':
                if data.get('agent_id') and data['agent_id'] != store.settings()['agent_id'] and data['agent_id'] not in {a['id'] for a in self.server.host.agents() if a.get('available', True)}:
                    raise ValueError('登録済みのエージェントを選んでください')
                if data.get('save_agent_id') and data['save_agent_id'] not in {a['id'] for a in self.server.host.agents() if a.get('available', True)}:
                    raise ValueError('利用できる保存担当AIを選んでください')
                previous = store.settings()
                settings = store.save_settings(data)
                if settings['discovery_prompt'] != previous['discovery_prompt']:
                    self.server.discovery.request_refresh()
                elif settings['discovery_enabled'] and {'discovery_enabled', 'discovery_interval_hours'} & data.keys():
                    self.server.discovery.trigger()
                if {'enabled', 'agent_id'} & data.keys():
                    self.server.enricher.restore_pending()
                self.send(200, settings)
            elif mutation and path == '/api/items':
                background = data.pop('background', False)
                if type(background) is not bool:
                    raise ValueError('backgroundはbooleanです')
                if background:
                    item, created = store.add_article(data)
                    if item['content_origin'] == 'manual' and not item.get('content_text'):
                        item = self.server.articles.start(item['id'])
                else:
                    item, created = self.server.articles.add(data)
                if item.get('fetch_state') != 'fetching' and (item['summary'] or item.get('content_text')):
                    self.server.enricher.enqueue([item])
                self.send(201 if created else 200, {'item': item, 'created': created})
            elif not mutation and path == "/api/sources":
                self.send(200, {"sources": store.sources()})
            elif not mutation and path == '/api/source-adapters':
                self.send(200, {'adapters': adapter_options()})
            elif not mutation and path == "/api/profile":
                self.send(200, store.profile())
            elif mutation and path == "/api/sources":
                source_id = store.add_source(data.get("name"), data.get("url", ""), data.get("kind", "rss"), data.get('adapter', 'rss'))
                self.send(201, store.source(source_id))
            elif mutation and path.startswith('/api/sources/') and path.endswith('/delete'):
                parts = path.split('/')
                if len(parts) != 5 or set(data) != {'confirm'} or data.get('confirm') is not True:
                    raise ValueError('配信元を削除する確認が必要です')
                store.delete_source(int(parts[3]))
                self.send(200, {'deleted': True})
            elif mutation and path.startswith("/api/sources/"):
                source_id = int(path.split("/")[-1])
                if type(data.get("enabled")) is not bool:
                    raise ValueError("enabledはbooleanです")
                store.enable_source(source_id, data["enabled"])
                self.send(200, store.source(source_id))
            elif mutation and path == "/api/refresh":
                started = self.server.collector.trigger()
                self.send(202, {"started": started, **self.server.collector.status()})
            elif mutation and path == "/api/profile/reset":
                if data.get("confirm") is not True:
                    raise ValueError("評価をリセットする確認が必要です")
                store.reset_profile()
                self.send(200, store.profile())
            elif path.startswith("/api/items/"):
                parts = path.split("/")
                if not mutation and len(parts) == 4:
                    item = store.item(parts[3])
                    cached = self.server.previews.cached_for([item])
                    if item['id'] in cached: item['link_preview'] = cached[item['id']]
                    self.send(200, item)
                elif not mutation and len(parts) == 5 and parts[4] == 'preview':
                    self.send(200, self.server.previews.request(parts[3]))
                elif not mutation and len(parts) == 6 and parts[4:] == ['preview', 'image']:
                    image, mime = self.server.previews.image(parts[3])
                    self.send(200, image, mime)
                elif not mutation and len(parts) == 5 and parts[4] == 'evidence':
                    self.send(200, search_evidence(self.server.host, store.item(parts[3])))
                elif mutation and len(parts) == 5 and parts[4] == 'open':
                    if data:
                        raise ValueError('リンク読了は保存済み設定に従います。追加オプションは不要です')
                    self.send(200, store.open_item(parts[3]))
                elif mutation and len(parts) == 5 and parts[4] == "react":
                    self.send(200, store.react(parts[3], data))
                elif mutation and len(parts) == 5 and parts[4] == 'tags':
                    if len(data) != 1 or next(iter(data)) not in {'add', 'remove'}:
                        raise ValueError('addまたはremoveを一つ指定してください')
                    operation = next(iter(data))
                    self.send(200, store.change_tag(parts[3], operation, data[operation]))
                elif mutation and len(parts) == 5 and parts[4] == 'fetch':
                    if data.keys() - {'background'} or type(data.get('background', False)) is not bool:
                        raise ValueError('backgroundはbooleanです')
                    item = self.server.articles.start(parts[3]) if data.get('background') else self.server.articles.fetch(parts[3])
                    if item.get('fetch_state') != 'fetching' and (item['summary'] or item.get('content_text')):
                        self.server.enricher.enqueue([item], retry=True)
                    self.send(200, item)
                elif mutation and len(parts) == 5 and parts[4] == 'note':
                    if set(data) != {'note'}:
                        raise ValueError('noteだけを指定してください')
                    self.send(200, store.save_note(parts[3], data['note']))
                elif mutation and len(parts) == 5 and parts[4] == 'save':
                    if data.keys() != {'confirm', 'request_id'} or data.get('confirm') is not True:
                        raise ValueError('保存AIへ依頼する確認とrequest_idが必要です')
                    self.send(200, self.server.saving.request(parts[3], data['request_id']))
                elif mutation and len(parts) == 5 and parts[4] == 'save-result':
                    self.send(200, self.server.saving.report(parts[3], data))
                elif mutation and len(parts) == 5 and parts[4] == "discuss":
                    if data.keys() - {'prompt', 'agent_id', 'new_conversation', 'request_id'}:
                        raise ValueError('未対応の議論オプションです')
                    self.send(200, self.server.discussions.open(parts[3], **data))
                elif mutation and len(parts) == 5 and parts[4] == 'analyze':
                    item = store.item(parts[3])
                    queued = self.server.enricher.enqueue([item], retry=True)
                    if not queued:
                        raise ValueError('AI設定が未完了か、解説を処理中です。「配信元・好み」で確認してください')
                    self.send(202, {'queued': queued})
                else:
                    self.send(404, {"error": "見つかりません"})
            else:
                self.send(404, {"error": "見つかりません"})
        except KeyError as error:
            self.send(404, {"error": str(error)})
        except sqlite3.IntegrityError:
            self.send(409, {"error": "この配信元は登録済みです"})
        except (ValueError, TypeError, AttributeError) as error:
            self.send(400, {"error": str(error)})
        except Exception as error:
            # Do not return host credentials or potentially sensitive request headers.
            print(f"request failed: {type(error).__name__}", file=sys.stderr)
            self.send(502, {"error": "処理に失敗しました。配信元の状態またはxangi接続を確認してください"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["serve"])
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    token = os.environ.get("XANGI_EXTENSION_AUTH_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("xangiから起動してください: XANGI_EXTENSION_AUTH_TOKEN is required")
    workspace = args.workspace.resolve()
    host = HostClient(os.environ.get("XANGI_URL") or os.environ.get("XANGI_EXTENSION_HOST_URL", ""),
                      os.environ.get("XANGI_TOKEN") or os.environ.get("XANGI_EVEN_TERMINAL_TOKEN", ""),
                      os.environ.get("XANGI_FEED_AGENT_ID", ""))
    store = Store(workspace / '.xangi-feed' / 'feed.sqlite3')
    server = Server(store, token, host)
    stopping = threading.Event()

    def stop(*_):
        stopping.set()

    def parent_watch():
        sys.stdin.buffer.read()
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threading.Thread(target=parent_watch, daemon=True).start()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    print(json.dumps({"schemaVersion": 2, "event": "ready", "id": "xangi-feed",
                      "baseUrl": f"http://127.0.0.1:{server.server_port}", "workspace": str(workspace)}), flush=True)
    server.enricher.configure_agent()
    server.enricher.start()
    server.collector.start()
    server.discovery.start()
    stopping.wait()
    server.shutdown()
    worker.join()
    server.server_close()
    server.collector.close()
    server.enricher.close()
    store.close()


if __name__ == "__main__":
    main()
