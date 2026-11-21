"""Delayed reaction responses, rollback, DOM identity, and view-switch races."""
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store, RECOMMENDATION_DEFAULTS, VIEW_DEFAULTS


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        store = Store(Path(tmp) / 'db', seed=False)
        store.save_settings({'enabled': False, 'show_link_previews': False})
        source = store.source(store.add_source('Fixture', 'https://example.org/rss', 'blog'))
        store.ingest(source, [dict(url=f'https://example.org/{i}', title=f'Article {i}', summary='Fixture', topics=['AI'], published=100+i) for i in range(5)])
        view = store.save_custom_filter({'name': 'All fixture', 'kinds': ['blog'], 'recommendations': dict(RECOMMENDATION_DEFAULTS), 'conditions': {**VIEW_DEFAULTS, 'read': 'any', 'muted': 'include'}})
        other_view = store.save_custom_filter({'name': 'Other fixture', 'kinds': ['blog'], 'recommendations': dict(RECOMMENDATION_DEFAULTS), 'conditions': {**VIEW_DEFAULTS, 'read': 'any', 'muted': 'include'}})
        server = Server(store, 'fixture', host=Mock(configured=False)); server.enricher.enqueue = Mock(return_value=0)
        worker = threading.Thread(target=server.serve_forever); worker.start()
        browser = p.chromium.launch(executable_path='/usr/bin/chromium-browser', headless=True)
        try:
            for width in [1280, 393]:
                page = browser.new_page(viewport={'width': width, 'height': 900}, extra_http_headers={'Authorization': 'Bearer fixture'})
                errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
                page.add_init_script('''window.reactionCalls = 0; window.listCalls = 0; window.gates = []; window.failReaction = false;
                const originalFetch = window.fetch;
                window.fetch = async (...args) => {
                  const path = new URL(args[0]).pathname;
                  if (path === '/api/items') window.listCalls++;
                  if (path.endsWith('/react')) {
                    window.reactionCalls++;
                    await new Promise(resolve => window.gates.push(resolve));
                    if (window.failReaction) return new Response(JSON.stringify({error:'fixture failure'}), {status:503});
                  }
                  return originalFetch(...args);
                };''')
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.evaluate('(id) => selectCustomFilter(id)', view['id'])
                expect(page.locator('#feed .news')).to_have_count(5)
                row = page.locator('#feed .news').first
                item_id = row.get_attribute('data-id')
                store.react(item_id, {'rating': 0, 'read': False, 'saved': False})
                page.evaluate('() => load()')
                row = page.locator(f'#feed .news[data-id="{item_id}"]')
                page.evaluate('''() => {window.keptRows = [...document.querySelectorAll('#feed .news')]; window.keptEditorial = document.querySelector('#feed .news .editorial'); window.initialLists = window.listCalls;}''')
                good = row.locator('.rating-control:not(.bad)'); bad = row.locator('.rating-control.bad')
                good.click()
                expect(good).to_have_attribute('aria-pressed', 'true', timeout=700)
                expect(bad).to_be_disabled()
                assert store.item(item_id)['rating'] == 0
                page.evaluate('() => window.gates.shift()()')
                expect(good).to_be_enabled(); assert store.item(item_id)['rating'] == 1
                assert page.evaluate('() => window.keptRows.every(row => row.isConnected) && window.keptEditorial.isConnected && window.initialLists === window.listCalls')
                page.evaluate('() => window.failReaction = true')
                bad.click(); expect(bad).to_have_attribute('aria-pressed', 'true', timeout=700)
                page.evaluate('() => window.gates.shift()()')
                expect(good).to_have_attribute('aria-pressed', 'true'); expect(bad).to_be_enabled()
                expect(page.locator('#notice')).to_contain_text('操作前の状態へ戻しました')
                assert store.item(item_id)['rating'] == 1
                page.evaluate('() => window.failReaction = false')
                read = row.locator('.read-control')
                read.click(); expect(read).to_have_attribute('aria-pressed', 'true', timeout=700)
                page.evaluate('() => window.gates.shift()()'); expect(read).to_be_enabled()
                assert store.item(item_id)['read'] == 1
                # Refresh during an in-flight request must preserve pending state and rollback.
                page.evaluate('() => window.failReaction = true')
                bad.click(); page.evaluate('() => load()')
                expect(bad).to_have_attribute('aria-pressed', 'true'); expect(bad).to_be_disabled()
                page.evaluate('() => window.gates.shift()()')
                expect(good).to_have_attribute('aria-pressed', 'true'); expect(bad).to_be_enabled()
                # Detail keeps an unsaved note while reaction controls update in place.
                page.evaluate('() => window.failReaction = false')
                page.evaluate('(id) => openDetail(state.items.find(item => item.id === id))', item_id)
                page.locator('#article-note').fill('unsaved note')
                detail_bad = page.locator('#detail .rating-control.bad')
                detail_bad.click(); expect(detail_bad).to_have_attribute('aria-pressed', 'true', timeout=700)
                expect(bad).to_have_attribute('aria-pressed', 'true')
                page.evaluate('() => window.gates.shift()()'); expect(detail_bad).to_be_enabled()
                expect(page.locator('#article-note')).to_have_value('unsaved note')
                reason = page.locator('#detail .reason select')
                reason.select_option('duplicate'); expect(reason).to_be_disabled()
                page.evaluate('() => window.gates.shift()()'); expect(reason).to_be_enabled()
                assert store.item(item_id)['reason'] == 'duplicate'
                expect(page.locator('#article-note')).to_have_value('unsaved note')
                page.locator('#detail-close').click()
                # An unrelated view must not be replaced or lose its DOM on completion.
                read.click(); expect(read).to_have_attribute('aria-pressed', 'false')
                page.evaluate('(id) => selectCustomFilter(id)', other_view['id'])
                expect(page.locator('#feed .news')).to_have_count(5)
                page.evaluate('() => window.switchedRows = [...document.querySelectorAll("#feed .news")]')
                page.evaluate('() => window.gates.shift()()')
                expect(page.locator(f'#feed .news[data-id="{item_id}"] .read-control')).to_be_enabled()
                expect(page.locator('#current-view-title')).to_have_text('Other fixture')
                assert page.evaluate('() => window.switchedRows.every(row => row.isConnected)')
                assert not errors, errors
                results.append({'width': width, 'optimistic_good_bad_read': True, 'rollback': True, 'stable_dom_no_list_fetch': True, 'pending_refresh_rollback': True, 'detail_note_preserved': True, 'reason': True, 'view_switch': True})
                page.screenshot(path=str(out / f'reactions-{width}.png'))
                page.close()
            (out / 'result.json').write_text(json.dumps(results, indent=2))
            print(json.dumps(results))
        finally:
            browser.close(); server.shutdown(); server.server_close(); worker.join()
            server.collector.close(); server.enricher.close(); store.close()


if __name__ == '__main__': main()
