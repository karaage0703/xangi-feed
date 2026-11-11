"""Fixture-only browser checks; uses an existing Playwright environment."""
import io
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store


class FixtureHost:
    configured = True
    agent_id = ''
    mode = 'ready'

    def request(self, path, **kwargs):
        assert path.startswith('/api/extensions/xangi-search/service/search?')
        time.sleep(0.15)
        if self.mode == 'error':
            raise TimeoutError('fixture-secret-must-not-appear')
        data = {'results': [] if self.mode == 'empty' else [
            {'file_path': 'projects/voice-robot.md', 'content': '音声対話の試作記録。\n<img src=x onerror=alert(1)>'},
            {'file_path': 'projects/' + 'long-' * 16 + '.md', 'content': 'マイクの実装条件を記録。'},
            {'file_path': 'notes/audio.md', 'content': '音声の処理時間の記録。'}]
        }
        if self.mode == 'degraded':
            data['degraded'] = True
        return io.BytesIO(json.dumps(data).encode())

    def agents(self):
        return []

    def run(self, *args, **kwargs):
        raise AssertionError('browser check must not call an LLM')


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / 'fixture.db', seed=False)
        item = store.add_article({'url': 'https://example.org/robot', 'title': '音声対話ロボットの試作'})[0]
        store.save_analysis(item['id'], store.content_hash(item), 'done', {
            'title_ja': item['title'], 'summary_ja': 'マイクで音声を受け取り、返事を話すロボット。',
            'comment': 'コメント'})
        with store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('settings',?)", (json.dumps({
                'recommendation_priority': 'balanced'}),))
        host = FixtureHost()
        server = Server(store, 'fixture-token', host=host)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        checks = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
                for width in [1280, 393, 360]:
                    store.react(item['id'], {'rating': 0})
                    host.mode = 'ready'; host.configured = True
                    page = browser.new_page(viewport={'width': width, 'height': 1000},
                                            extra_http_headers={'Authorization': 'Bearer fixture-token'})
                    errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                    page.add_init_script("localStorage.setItem('xangi-feed-sort', 'preference')")
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(page.locator('.news')).to_have_count(1)
                    expect(page.locator('#sort')).to_have_value('preference')
                    expect(page.get_by_text('AI重要度', exact=False)).to_have_count(0)
                    expect(page.get_by_text('古い個人判定は表示しない')).to_have_count(0)
                    row = page.locator('.news')
                    row.get_by_role('button', name=item['title'] + 'を好きと評価', exact=True).click()
                    expect(row.get_by_role('button', name=item['title'] + 'を好きと評価', exact=True)).to_have_attribute('aria-pressed', 'true')
                    row.locator('.more-actions > summary').click()
                    row.get_by_role('button', name='記事の詳細', exact=True).click()
                    section = page.locator('.recommendation-evidence')
                    search = section.get_by_role('button', name='推薦根拠を探す', exact=True)
                    search.focus(); search.press('Enter')
                    expect(section.get_by_role('button', name='関連する記録を検索中…')).to_be_disabled()
                    expect(section.get_by_text('projects/voice-robot.md', exact=True)).to_be_visible()
                    expect(section.locator('.recommendation-source')).to_have_count(3)
                    expect(section.locator('img')).to_have_count(0)
                    assert section.evaluate('(node) => node.scrollWidth <= node.clientWidth')
                    section.screenshot(path=str(output / f'{width}-ready.png'))
                    page.locator('#detail').screenshot(path=str(output / f'{width}-detail.png'))
                    host.mode = 'empty'
                    section.get_by_role('button', name='推薦根拠をもう一度探す').click()
                    expect(section.get_by_text('関連する記録は見つかりませんでした。', exact=False)).to_be_visible()
                    host.mode = 'error'
                    section.get_by_role('button', name='推薦根拠をもう一度探す').click()
                    expect(section.get_by_text('推薦根拠を取得できませんでした。', exact=False)).to_be_visible()
                    expect(section.get_by_text('fixture-secret', exact=False)).to_have_count(0)
                    section.screenshot(path=str(output / f'{width}-error.png'))
                    host.mode = 'degraded'
                    section.get_by_role('button', name='推薦根拠をもう一度探す').click()
                    expect(section.get_by_text('キーワード検索の結果', exact=False)).to_be_visible()
                    host.configured = False
                    section.get_by_role('button', name='推薦根拠をもう一度探す').click()
                    expect(section.get_by_text('接続が設定されていません', exact=False)).to_be_visible()
                    self_rating = store.item(item['id'])['rating']
                    assert self_rating == 1
                    assert store.profile()['good'] == 1
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.locator('#detail-close').click()
                    expect(page.locator('.news')).to_have_count(1)
                    expect(page.locator('#recommendation-form [name=recommendation_priority]')).to_be_enabled()
                    page.locator('#recommendation-toggle > summary').click()
                    page.locator('#recommendation-form [name=recommendation_priority]').select_option('interest')
                    expect(page.locator('#recommendation-message')).to_have_text('保存して反映しました。')
                    assert not errors, errors
                    checks.append({'width': width, 'states': ['ready', 'empty', 'error', 'degraded', 'unavailable'],
                                   'retry': True, 'good_preserved': self_rating, 'page_errors': errors})
                    page.close()
                browser.close()
            (output / 'result.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2))
            print(json.dumps(checks, ensure_ascii=False))
        finally:
            server.shutdown(); server.server_close(); thread.join()
            server.collector.close(); server.enricher.close(); store.close()


if __name__ == '__main__':
    main()
