"""Personal discovery UI with synthetic records; no live search or model calls."""
import io
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store
from xangi_feed.discovery import Discovery


class FixtureHost:
    configured = True
    agent_id = 'fixture'
    fail = False

    def agents(self):
        return [{'id': 'fixture', 'name': 'Fixture', 'available': True}]

    def request(self, path, **kwargs):
        assert path.startswith('/api/extensions/xangi-search/service/search?')
        if self.fail:
            raise TimeoutError('private-fixture-error')
        return io.BytesIO(json.dumps({'results': [
            {'file_path': 'notes/voice-robot.md', 'content': '架空の音声対話ロボットを試作中。<img src=x onerror=alert(1)>'}
        ]}).encode())

    def plan_search(self, prompt, *args, **kwargs):
        assert 'records' in prompt
        return json.dumps({'themes': [{'query': '音声 ロボット', 'reason': '音声対話の試作に使える実装例を探します。', 'source_ids': ['R1']}]})

    def run(self, *args, **kwargs):
        raise AssertionError('No real model calls')


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/snap/bin/chromium', args=['--no-sandbox'])
        for width in [1280, 390]:
            with tempfile.TemporaryDirectory() as tmp:
                store = Store(Path(tmp) / 'db', seed=False)
                store.save_settings({'enabled': False, 'agent_id': 'fixture', 'discovery_enabled': False})
                article = store.add_article({'title': 'robot', 'url': 'https://known.example/robot'})[0]
                store.react(article['id'], {'rating': 1})
                host = FixtureHost()
                server = Server(store, 'fixture-token', host=host)
                def search(*args, **kwargs):
                    return [{'id': 'candidate', 'url': 'https://new.example/robot', 'feed_url': 'https://new.example/rss',
                             'title': '音声で応答するロボットの実装', 'source_name': '架空の工作ノート',
                             'published': time.time(), 'matched_topics': [], 'matched_query': kwargs['plan']['queries'][0] if kwargs['plan']['queries'] else '',
                             'reason': '公開フィードで検索語に合う記事を確認しました。'}], {'searches_ok': 1}
                server.discovery = Discovery(store, host, searcher=search)
                server.discovery.trigger(force=True); server.discovery.worker.join(3)
                thread = threading.Thread(target=server.serve_forever); thread.start()
                context = browser.new_context(viewport={'width': width, 'height': 1000}, extra_http_headers={'Authorization': 'Bearer fixture-token'})
                page = context.new_page(); errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                context.route('**/*', lambda route: route.continue_() if route.request.url.startswith('http://127.0.0.1:') else route.abort())
                try:
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    page.get_by_role('button', name='おすすめ', exact=True).click()
                    page.locator('#discovery-toggle > summary').click()
                    toggle = page.get_by_role('checkbox', name='自分の記録を使う', exact=True)
                    expect(toggle).to_be_checked()
                    expect(page.locator('#feed .news')).to_contain_text('自分とのつながり: 音声対話の試作')
                    summary = page.get_by_text('検索テーマに使った記録', exact=True)
                    summary.focus(); page.keyboard.press('Enter')
                    expect(page.locator('#feed .news .discovery-context .excerpt')).to_be_visible()
                    expect(page.locator('#feed .news .discovery-context .excerpt')).to_contain_text('<img src=x onerror=alert(1)>')
                    expect(page.locator('#feed .news img')).to_have_count(0)
                    # Polling must not collapse a record the user is reading.
                    page.evaluate('loadDiscovery()')
                    expect(page.locator('#feed .news .discovery-context details')).to_have_attribute('open', '')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(output / f'personal-{width}.png'), full_page=True)
                    toggle.uncheck()
                    expect(page.locator('#discovery-save-message')).to_have_text('自動保存して反映しました。')
                    expect(page.locator('#discovery-details')).to_contain_text('自分の記録は使っていません。')
                    expect(page.locator('#feed .news .discovery-context details')).to_have_count(0)
                    page.reload()
                    page.get_by_role('button', name='おすすめ', exact=True).click()
                    page.locator('#discovery-toggle > summary').click()
                    expect(toggle).not_to_be_checked()
                    host.fail = True
                    toggle.check()
                    expect(page.locator('#discovery-details')).to_contain_text('通常の条件で探します。')
                    expect(page.locator('#discovery-message')).not_to_contain_text('private-fixture-error')
                    expect(page.locator('#feed .news .discovery-context details')).to_have_count(0)
                    page.screenshot(path=str(output / f'fallback-{width}.png'), full_page=True)
                    page.get_by_role('button', name='すべて', exact=True).click()
                    expect(page.locator('#feed')).to_be_visible()
                    assert not errors, errors
                    print(f'PASS {width}: personal theme, cited record, keyboard disclosure, escaped HTML, poll persistence, no overflow, toggle/reload, fallback, return to list')
                finally:
                    context.close(); server.shutdown(); server.server_close(); thread.join(); store.close()
        browser.close()


if __name__ == '__main__':
    main()
