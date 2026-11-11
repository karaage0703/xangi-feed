"""Fixture UI checks for concurrent startup, failures and state preservation.

Uses the existing Playwright/Chromium environment; no external service calls.
"""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / 'fixture.sqlite3', seed=False)
        sources = [store.source(store.add_source(name, f'https://example.org/feed/{i}', 'blog'))
                   for i, name in enumerate(['工作ブログ', '技術ブログ'])]
        for i, source in enumerate(sources):
            store.ingest(source, [dict(url=f'https://example.org/{i}', title=f'表示検証{i}',
                                       summary='固定fixture', topics=['ロボット'], published=1790000000+i)])
        items = store.list_items(tab='all')['items']
        saved = next(item for item in items if item['title'] == '表示検証1')
        store.react(saved['id'], {'saved': True})
        server = Server(store, 'performance-fixture-token', host=Mock(configured=False))
        server.enricher.enqueue = Mock(return_value=0)
        collector_status = {'running': False, 'last_finished': None, 'added': 0}
        server.collector.status = Mock(side_effect=lambda: dict(collector_status))
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        checks = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))

                def new_page(width=1280, delays=None, failure=None):
                    page = browser.new_page(viewport={'width': width, 'height': 900},
                                            extra_http_headers={'Authorization': 'Bearer performance-fixture-token'})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    preferences = {tab: dict(kinds=['blog'], topics=['ロボット'], tags=[],
                                            source=str(source['id']), search='', sourceSearch='')
                                   for tab, source in [('recommended', sources[0]), ('saved', sources[1])]}
                    page.add_init_script('''(() => {
                      const preferences = PREFERENCES;
                      for (const [tab, value] of Object.entries(preferences)) localStorage.setItem('xangi-feed-classification:' + tab, JSON.stringify(value));
                      window.__delays = DELAYS;
                      window.__failure = FAILURE;
                      window.__calls = [];
                      window.__unhandled = [];
                      window.addEventListener('unhandledrejection', event => window.__unhandled.push(String(event.reason)));
                      const original = window.fetch;
                      window.fetch = async (...args) => {
                        const path = new URL(args[0]).pathname;
                        const call = {path, url: String(args[0]), start: performance.now()};
                        window.__calls.push(call);
                        await new Promise(resolve => setTimeout(resolve, window.__delays[path] || 0));
                        const response = path === window.__failure ? new Response(JSON.stringify({error: 'fixture failure ' + path}), {status: 503}) : await original(...args);
                        call.end = performance.now();
                        return response;
                      };
                    })();'''.replace('PREFERENCES', json.dumps(preferences, ensure_ascii=False))
                        .replace('DELAYS', json.dumps(delays or {})).replace('FAILURE', json.dumps(failure)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    return page, errors

                for width in [1280, 393, 360]:
                    for slow in ['/api/sources', '/api/filter-options']:
                        page, errors = new_page(width, {slow: 350, '/api/filter-options' if slow == '/api/sources' else '/api/sources': 200})
                        expect(page.locator('#feed .news')).to_have_count(1)
                        expect(page.locator('#feed .news')).to_contain_text('表示検証0')
                        expect(page.locator('#source')).to_have_value(str(sources[0]['id']))
                        calls = page.evaluate('() => window.__calls')
                        inputs = [next(call for call in calls if call['path'] == path) for path in ['/api/sources', '/api/filter-options']]
                        item_call = next(call for call in calls if call['path'] == '/api/items')
                        assert max(call['start'] for call in inputs) < min(call['end'] for call in inputs), inputs
                        assert item_call['start'] >= max(call['end'] for call in inputs), calls
                        assert 'source=' + str(sources[0]['id']) in item_call['url']
                        assert 'kind=blog' in item_call['url']
                        assert not errors and not page.evaluate('() => window.__unhandled'), errors
                        checks.append({'width': width, 'slow': slow, 'overlap_and_restore_before_items': True})
                        page.close()

                startup_paths = ['/api/health', '/api/sources', '/api/filter-options', '/api/recommendations']
                page, errors = new_page(delays={path: 250 for path in startup_paths})
                expect(page.locator('#feed .news')).to_have_count(1)
                calls = page.evaluate('() => window.__calls')
                inputs = [next(call for call in calls if call['path'] == path) for path in startup_paths]
                item_call = next(call for call in calls if call['path'] == '/api/items')
                assert max(call['start'] for call in inputs) < min(call['end'] for call in inputs), inputs
                assert item_call['start'] >= max(call['end'] for call in inputs), calls
                assert not errors and not page.evaluate('() => window.__unhandled'), errors
                checks.append({'all_four_startup_inputs_overlap_before_items': True})
                page.close()

                for failure in ['/api/sources', '/api/filter-options', '/api/recommendations', '/api/health']:
                    page, errors = new_page(failure=failure)
                    if failure in ['/api/sources', '/api/filter-options']:
                        expect(page.locator('#notice')).to_contain_text('fixture failure ' + failure)
                        page.wait_for_timeout(100)
                        assert not any(call['path'] == '/api/items' for call in page.evaluate('() => window.__calls'))
                        assert json.loads(page.evaluate("localStorage.getItem('xangi-feed-classification:recommended')"))['source'] == str(sources[0]['id'])
                    else:
                        expect(page.locator('#feed .news')).to_have_count(1)
                        if failure == '/api/recommendations':
                            expect(page.locator('#recommendation-retry')).to_be_visible()
                            expect(page.locator('#recommendation-fields')).to_have_js_property('disabled', True)
                            page.evaluate('() => window.__failure = null')
                            page.locator('#recommendation-retry').click()
                            expect(page.locator('#recommendation-fields')).to_have_js_property('disabled', False)
                            expect(page.locator('#recommendation-retry')).to_be_hidden()
                        else:
                            expect(page.locator('#sync-status')).to_have_text('接続を確認してください')
                    assert not errors and not page.evaluate('() => window.__unhandled'), errors
                    checks.append({'failure': failure, 'failure_handled_without_unhandled_rejection': True})
                    page.close()

                page, errors = new_page(delays={'/api/sources': 600, '/api/filter-options': 400, '/api/items': 250})
                page.wait_for_function("() => window.__calls.some(call => call.path === '/api/filter-options')")
                page.locator('[data-tab=saved]').click()
                expect(page.locator('#source')).to_have_value(str(sources[1]['id']))
                expect(page.locator('#feed .news')).to_have_count(1)
                expect(page.locator('#feed .news')).to_contain_text('表示検証1')
                expect(page.locator('[data-tab=saved]')).to_have_attribute('aria-pressed', 'true')
                assert json.loads(page.evaluate("localStorage.getItem('xangi-feed-classification:recommended')"))['source'] == str(sources[0]['id'])
                page.locator('[data-tab=recommended]').click()
                expect(page.locator('#feed .news')).to_contain_text('表示検証0')
                assert not errors and not page.evaluate('() => window.__unhandled'), errors
                checks.append({'tab_change_during_startup_and_stale_response': True})
                page.close()

                page, errors = new_page()
                expect(page.locator('#feed .news')).to_have_count(1)
                original_ids = page.locator('#feed .news').evaluate_all('(rows) => rows.map(row => row.dataset.id)')
                store.ingest(sources[0], [dict(url='https://example.org/new', title='後から届く記事', summary='新着fixture', topics=['ロボット'], published=1791000000)])
                collector_status.update(last_finished=1791000000, added=1)
                page.evaluate('() => poll()')
                expect(page.locator('#new-items')).to_be_visible()
                assert page.locator('#feed .news').evaluate_all('(rows) => rows.map(row => row.dataset.id)') == original_ids
                page.locator('#new-items').click()
                expect(page.locator('#feed .news')).to_have_count(2)
                expect(page.locator('#feed .news').first).to_contain_text('後から届く記事')
                assert not errors and not page.evaluate('() => window.__unhandled'), errors
                checks.append({'poll_preserves_order_until_explicit_refresh': True})
                page.screenshot(path=str(output / 'initial-ui.png'), full_page=True)
                page.close()
                browser.close()
            (output / 'result.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2))
            print(json.dumps({'result': 'passed', 'checks': checks}, ensure_ascii=False))
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
            server.collector.close()
            server.enricher.close()
            store.close()


if __name__ == '__main__':
    main()
