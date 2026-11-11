"""Optional integration check using an already installed Playwright + Chromium.

Run with a Python environment containing playwright:
  python tests/browser_check.py /absolute/path/to/evidence
Only temporary fixture data and a mock xangi host are used.
"""
import json
import sys
import tempfile
import threading
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect, sync_playwright

from xangi_feed.feeds import parse_feed
from xangi_feed.server import ENTRY_HTML, Handler, Server
from xangi_feed.service import Collector, HostClient
from xangi_feed.store import Store, RECOMMENDATION_DEFAULTS
from test_notes import PARENT

TOKEN = 'test-news-token-' * 3
PREFIX = '/api/extensions/xangi-feed/service/'


class MockHost(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == '/api/agents':
            self.reply({'agents': [
                {'id': 'fixture-local', 'name': '検証用ローカル', 'backend': 'local-llm', 'model': 'fixture-local-model', 'available': True},
                {'id': 'fixture-second', 'name': '検証用別担当・長いエージェント名でスマホ幅を検証', 'backend': 'fixture-backend', 'model': 'fixture-model-2', 'available': True},
                {'id': 'fixture-unavailable', 'name': '検証用利用不可', 'available': False}]})
        else:
            self.reply({'lifecycle': 'open'})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.calls.append((self.path, data))
        if self.path == '/api/sessions':
            self.reply({'sessionId': 'test-s1'})
        elif self.path == '/api/chat':
            raw = b'data: {"type":"done"}\n\n'
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        else:
            self.send_error(404)

    def reply(self, data):
        raw = json.dumps(data).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)


class FixtureProxy(Handler):
    def handle_request(self, mutation):
        origin = self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            self.send(403, {'error': 'cross-origin extension requests are not allowed'})
            return
        if self.path == '/api/extensions/xangi-feed/ui':
            body = ENTRY_HTML.replace('<head>', '<head><base href="./service/">').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'self'")
            self.end_headers(); self.wfile.write(body)
            return
        if self.path.startswith('/chat/'):
            self.send(200, b'<!doctype html><title>Mock xangi conversation</title><p>Conversation test-s1</p>', 'text/html')
            return
        if not self.path.startswith(PREFIX):
            self.send(404, {'error': 'fixture proxy path required'})
            return
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.send(403, {'error': 'cross-origin extension requests are not allowed'})
            return
        self.path = '/' + self.path[len(PREFIX):]
        self.headers['Authorization'] = 'Bearer ' + TOKEN
        super().handle_request(mutation)


def menu_click(page, row, name=None, selector=None):
    row.locator('.more-actions summary').click()
    target = row.locator(selector) if selector else row.get_by_role('button', name=name, exact=True)
    target.click()


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / 'news.db', seed=False)
        store.save_settings({'mark_read_on_open': False})
        source = store.source(store.add_source('検証用フィード', 'https://example.org/feed', 'rss'))
        titles = ['ローカルLLMの省メモリ運用を試す', 'A robot learns a new motion', '工作と音声配信の新着']
        for i, title in enumerate(titles):
            xml = f'<rss><channel><item><title>{title}</title><link>https://example.org/{i}</link><description>小さく試して、手元の環境で確かめる。ローカルAIとロボットの実装について、新しい手法と具体的な使い方を紹介します。</description><pubDate>Tue, 03 Nov 2026 09:00:00 GMT</pubDate></item></channel></rss>'
            store.ingest(source, parse_feed(xml.encode(), source['url']))
        item_id = store.list_items(tab='all')['items'][0]['id']
        store.save_analysis(item_id, store.content_hash(store.item(item_id)), 'done', {
            'title_ja': '工作と音声配信の新着', 'summary_ja': 'ローカルAIを使った工作と音声配信の実例。手元で試せる実装手順を中心に紹介しています。',
            'comment': '小さな試作品から始めると、手元のロボットにも応用しやすそうです。',
            'reason': 'ものづくりと実装への関心に合う',
        })
        mock = ThreadingHTTPServer(('127.0.0.1', 0), MockHost)
        host_thread = threading.Thread(target=mock.serve_forever); host_thread.start()
        collector = Collector(store, lambda _: b'<rss><channel></channel></rss>')
        server = Server(store, TOKEN, HostClient(f'http://127.0.0.1:{mock.server_port}', 'fixture-token'), collector)
        server.articles.fetcher = lambda url: b'<html><title>Fixture article</title><article>Fixture article body.</article></html>'
        store.save_settings({'enabled': False})
        server.RequestHandlerClass = FixtureProxy
        thread = threading.Thread(target=server.serve_forever); thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        results = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                for width, height, label in [(1280, 900, 'desktop'), (390, 844, 'mobile'), (360, 780, 'small-mobile')]:
                    store.react(item_id, {'rating': 0, 'read': False, 'saved': False})
                    store.save_note(item_id, '')
                    page = browser.new_page(viewport={'width': width, 'height': height})
                    errors, dialogs = [], []
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    page.on('dialog', lambda d: (dialogs.append(d.type), d.dismiss()))
                    page.context.route('https://example.org/**', lambda route: route.fulfill(status=200, content_type='text/html', body='<p>Original fixture</p>'))
                    page.goto(origin + '/api/extensions/xangi-feed/ui')
                    page.wait_for_url('**' + PREFIX)
                    row = page.locator(f'.news[data-id="{item_id}"]')
                    expect(row).to_be_visible()
                    expect(row.locator('.comment-label')).to_be_visible()
                    expect(row.locator('.personal-comment')).to_be_visible()
                    expect(row.locator('.personal-comment')).to_have_text('小さな試作品から始めると、手元のロボットにも応用しやすそうです。')
                    expect(row.locator('.comment-detail > .evidence')).not_to_be_visible()
                    expect(page.locator('.news')).to_have_count(3)
                    expect(row.locator('.compact-actions')).to_be_visible()
                    # Exactly six unobtrusive toolbar controls, no permanent AI/read/discussion buttons.
                    assert row.locator('.compact-actions>button').count() == 5
                    assert row.locator('.more-actions summary').count() == 1
                    expect(row.locator('.read-control')).not_to_be_visible()
                    expect(row.get_by_role('button', name='AI解説を作り直す', exact=True)).to_have_count(0)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'page overflow'
                    assert row.locator('.compact-actions').evaluate('(e) => e.scrollWidth <= e.clientWidth'), 'toolbar overflow'
                    toolbar = row.locator('.compact-actions').bounding_box()
                    for control in row.locator('.compact-actions>button, .more-actions>summary').all():
                        box = control.bounding_box()
                        assert box['height'] >= 44 and box['width'] >= 40
                        assert abs(box['y'] + box['height'] / 2 - toolbar['y'] - toolbar['height'] / 2) <= 1, ('toolbar alignment', box, toolbar)
                    page.screenshot(path=str(output / f'{label}-list.png'), full_page=True)
                    # Native menu supports keyboard open/escape and offers secondary actions.
                    row.locator('.more-actions summary').focus()
                    page.keyboard.press('Enter')
                    expect(row.locator('.read-control')).to_be_visible()
                    page.keyboard.press('Escape')
                    expect(row.locator('.read-control')).not_to_be_visible()
                    good = row.locator('button[aria-label$="を好きと評価"]')
                    bad = row.locator('button[aria-label$="を興味なしと評価"]')
                    good.click(); expect(good).to_have_attribute('aria-pressed', 'true')
                    good.click(); expect(good).to_have_attribute('aria-pressed', 'false')
                    bad.click(); expect(bad).to_have_attribute('aria-pressed', 'true')
                    bad.click(); expect(bad).to_have_attribute('aria-pressed', 'false')
                    # Keep original-link navigation and feed position.
                    with page.expect_popup() as opened:
                        row.locator('.news-title').click()
                    opened.value.close()
                    assert page.url == origin + PREFIX
                    # A single click delegates saving; acceptance is not completion.
                    with page.expect_response(lambda r: r.url.endswith('/save') and r.request.method == 'POST') as response:
                        row.locator('[data-action=notion]').click()
                    assert response.value.status == 200
                    expect(row.locator('.notion-status')).to_contain_text('保存AIへ依頼済み')
                    expect(page.locator('#detail')).not_to_be_visible()
                    assert not dialogs, dialogs
                    page.reload(); expect(row.locator('.notion-status')).to_contain_text('保存AIへ依頼済み')
                    expect(row.get_by_role('link', name='保存用の会話を開く')).to_be_visible()
                    row.get_by_role('button', name='メモを書く', exact=True).click()
                    expect(page.locator('#article-note')).to_be_focused()
                    note = '後で試す工作 ' + label + ' <img src=x onerror=alert(1)>'
                    page.locator('#article-note').fill(note)
                    page.locator('#detail .read-control').click()
                    expect(page.locator('#article-note')).to_have_value(note)
                    page.locator('#detail .read-control').click()
                    page.locator('#detail-close').click()
                    # Pending clicks reuse the same AI request; drafts still persist.
                    before_send = len(MockHost.calls)
                    with page.expect_response(lambda r: r.url.endswith('/save') and r.request.method == 'POST') as response:
                        row.locator('[data-action=notion]').click()
                    assert response.value.status == 200
                    expect(row.locator('.notion-status')).to_contain_text('保存AIへ依頼済み')
                    assert store.item(item_id)['note'] == note
                    assert len(MockHost.calls) == before_send
                    assert not dialogs
                    page.get_by_role('button', name='メモ', exact=True).click()
                    expect(page.locator('.news')).to_have_count(1)
                    row.get_by_role('button', name='メモを編集', exact=True).click()
                    expect(page.locator('#article-note')).to_have_value(note)
                    expect(page.locator('#detail img')).to_have_count(0)
                    page.screenshot(path=str(output / f'{label}-note.png'), full_page=False)
                    page.locator('#detail-close').click()
                    # Menu mark-as-read still removes the row from recommended and can undo in Read.
                    page.get_by_role('button', name='カスタム', exact=True).click()
                    menu_click(page, row, selector='.read-control')
                    expect(row).to_have_count(0)
                    page.get_by_role('button', name='読了', exact=True).click()
                    menu_click(page, row, selector='.read-control')
                    expect(row).to_have_count(0)
                    page.get_by_role('button', name='すべて', exact=True).click()
                    # Existing discussion behavior, including no automatic re-send.
                    menu_click(page, row, name='前の会話を開く' if store.item(item_id)['session_id'] else 'xangiと話す')
                    expect(page.locator('#discussion-form [type=submit]')).to_be_enabled()
                    before_send = len(MockHost.calls)
                    with page.expect_popup() as opened:
                        page.locator('#discussion-form [type=submit]').click()
                    opened.value.wait_for_url('**/chat/test-s1'); opened.value.close()
                    if label != 'desktop': assert len(MockHost.calls) == before_send
                    menu_click(page, row, name='前の会話を開く')
                    expect(page.locator('#discussion-form [type=submit]')).to_be_enabled()
                    before_send = len(MockHost.calls)
                    with page.expect_popup() as opened:
                        page.locator('#discussion-form [type=submit]').click()
                    opened.value.wait_for_url('**/chat/test-s1'); opened.value.close()
                    assert len(MockHost.calls) == before_send
                    # Saving configuration is independent of the analysis agent.
                    page.get_by_role('button', name='設定', exact=True).click()
                    expect(page.locator('#save-form')).to_contain_text('保存担当AI')
                    page.get_by_role('button', name='保存担当とスキルを設定', exact=True).click()
                    expect(page.locator('#save-message')).to_contain_text('設定しました')
                    page.locator('#settings-close').click()
                    assert not errors, errors
                    results.append({'viewport': label, 'single_click_no_confirm': True, 'toolbar_one_row': True, 'draft_saved': True, 'saving_conversation': True, 'console_errors': errors})
                    page.close()
                # Concurrent clicks across articles queue; a double click never submits twice.
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                queued_requests = []
                page.on('request', lambda request: queued_requests.append(request.url) if request.url.endswith('/save') and request.method == 'POST' else None)
                page.goto(origin + PREFIX)
                page.get_by_role('button', name='すべて', exact=True).click()
                expect(page.locator('.news')).to_have_count(3)
                page.locator('.news [data-action=notion]').evaluate_all('(buttons) => { buttons[0].click(); buttons[0].click(); buttons[1].click(); buttons[2].click(); }')
                expect(page.locator('.news .notion-status').filter(has_text='保存AIへ依頼済み')).to_have_count(3)
                assert len(queued_requests) == 3
                page.close()
                # Manual article flow: desktop and narrow mobile, then reopen as duplicate.
                for label, viewport in [('desktop', {'width': 1280, 'height': 900}), ('mobile', {'width': 393, 'height': 852})]:
                    page = browser.new_page(viewport=viewport)
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(origin + PREFIX)
                    page.get_by_role('button', name='記事を追加', exact=True).click()
                    expect(page.locator('#article-form input[name=url]')).to_be_focused()
                    page.locator('#article-form input[name=url]').fill('https://example.org/manual-' + label)
                    page.locator('#article-form input[name=title]').fill('個別に追加した工作記事')
                    page.locator('#article-form textarea').fill('手元で試したいロボットの工作メモ')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'manual form overflow'
                    page.screenshot(path=str(output / f'{label}-article-add.png'), full_page=False)
                    page.get_by_role('button', name='追加して開く', exact=True).click()
                    expect(page.locator('#detail')).to_be_visible()
                    expect(page.locator('#detail-body')).to_contain_text('個別に追加した工作記事')
                    expect(page.locator('#detail-body')).to_contain_text('公開ページから取得した本文')
                    page.screenshot(path=str(output / f'{label}-manual-detail.png'), full_page=False)
                    page.locator('#detail-close').click()
                    page.get_by_role('button', name='記事を追加', exact=True).click()
                    page.locator('#article-form input[name=url]').fill('https://example.org/manual-' + label + '?utm_source=again')
                    page.get_by_role('button', name='追加して開く', exact=True).click()
                    expect(page.locator('#notice')).to_contain_text('登録済みの記事')
                    expect(page.locator('#detail-body')).to_contain_text('個別に追加した工作記事')
                    assert not errors, errors
                    results.append({'viewport': label, 'manual_article_and_duplicate': True, 'console_errors': errors})
                    page.close()
                # Subscription registration, cancelled deletion, deletion and explicit re-add.
                for label, viewport in [('desktop', {'width': 1280, 'height': 900}), ('mobile', {'width': 393, 'height': 852})]:
                    page = browser.new_page(viewport=viewport)
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    decision = [False]
                    page.on('dialog', lambda dialog: dialog.accept() if decision[0] else dialog.dismiss())
                    page.goto(origin + PREFIX)
                    page.locator('#settings-open').click()
                    name, url = '削除・再追加の検証 ' + label, 'https://example.org/delete-' + label + '/rss'

                    def register():
                        page.locator('#source-form input[name=name]').fill(name)
                        page.locator('#source-form input[name=url]').fill(url)
                        page.locator('#source-form button[type=submit]').click()
                        expect(page.locator('#sources .source-row').filter(has_text=name)).to_have_count(1)

                    register()
                    registered = next(source for source in store.sources() if source['url'] == url)
                    store.ingest(registered, [{'url': url + '/article', 'title': '削除後も残す記事', 'summary': '抜粋', 'published': None, 'topics': []}])
                    retained = next(item for item in store.list_items(tab='all')['items'] if item['url'] == url + '/article')
                    store.save_note(retained['id'], '残すメモ')
                    row = page.locator('#sources .source-row').filter(has_text=name)
                    row.get_by_role('button', name='削除', exact=True).click()
                    expect(row).to_have_count(1)
                    row.scroll_into_view_if_needed()
                    assert page.locator('#settings').evaluate('(e) => e.scrollWidth <= e.clientWidth'), 'settings overflow'
                    page.screenshot(path=str(output / f'{label}-source-delete.png'), full_page=False)
                    decision[0] = True
                    row.get_by_role('button', name='削除', exact=True).click()
                    expect(row).to_have_count(0)
                    expect(page.locator('#source-message')).to_contain_text('配信元を削除しました')
                    assert store.item(retained['id'])['note'] == '残すメモ'
                    register()
                    assert next(source for source in store.sources() if source['url'] == url)['id'] == registered['id']
                    assert store.item(retained['id'])['note'] == '残すメモ'
                    assert not errors, errors
                    results.append({'viewport': label, 'source_add_cancel_delete_readd': True, 'console_errors': errors})
                    page.close()
                browser.close()
            result = {'browser': 'Chromium', 'checks': results, 'result': 'passed'}
            (output / 'browser-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps(result, ensure_ascii=False))
        finally:
            server.shutdown(); server.server_close(); thread.join(); collector.close()
            mock.shutdown(); mock.server_close(); host_thread.join(); store.close()


if __name__ == '__main__':
    main()
