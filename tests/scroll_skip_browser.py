"""Scroll skipping with real viewport geometry and fixture-only persistence."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / 'fixture.db', seed=False)
        ids = [store.add_article({'url': f'https://example.org/{i}', 'title': f'スクロール記事 {i}'})[0]['id'] for i in range(65)]
        server = Server(store, 'fixture-' * 8)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            results = []
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE'))
                for width in [1280, 393, 360]:
                    for item_id in ids:
                        store.react(item_id, {'muted': False, 'read': False, 'saved': False})
                    context = browser.new_context(viewport={'width': width, 'height': 800}, extra_http_headers={'Authorization': 'Bearer ' + 'fixture-' * 8})
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(page.locator('.news')).to_have_count(50)
                    row = page.locator('.news').first
                    first = row.get_attribute('data-id')
                    def top():
                        page.evaluate('window.scrollTo(0, 0)')
                        page.wait_for_timeout(150)
                    def view(target):
                        target.scroll_into_view_if_needed()
                        page.wait_for_timeout(150)
                    def pass_row(target):
                        page.evaluate('(el) => window.scrollBy(0, el.getBoundingClientRect().bottom + 20)', target.element_handle())
                        page.wait_for_timeout(250)
                    view(row)
                    pass_row(row)
                    assert not store.item(first)['muted'], 'default OFF'
                    top()
                    page.locator('#settings-open').click()
                    expect(page.locator('#scroll-skip')).not_to_be_checked()
                    page.locator('#scroll-skip').check()
                    page.screenshot(path=str(output / f'{width}-settings.png'))
                    page.locator('#settings-close').click()
                    page.reload()
                    expect(page.locator('.news')).to_have_count(50)
                    assert page.evaluate("localStorage.getItem('xangi-feed-scroll-skip')") == 'on'
                    top()
                    row = page.locator(f'article[data-id="{first}"]')
                    view(row)
                    before_height = row.bounding_box()['height']
                    before = store.item(first)
                    profile = store.profile()
                    pass_row(row)
                    expect(row).to_have_attribute('data-scroll-skipped', 'true')
                    assert store.item(first)['muted']
                    assert row.bounding_box()['height'] == before_height
                    for key in ['read', 'read_at', 'rating', 'saved', 'note', 'tags']:
                        assert store.item(first)[key] == before[key], key
                    assert store.profile() == profile
                    # An unseen row jumped over in one scroll must remain unchanged.
                    unseen = page.locator('.news').nth(15).get_attribute('data-id')
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    page.wait_for_timeout(250)
                    assert not store.item(unseen)['muted']
                    # Pagination must not skip a row because retained cards occupy DOM space.
                    page.locator('#more').click()
                    expect(page.locator('#more')).to_be_hidden()
                    remaining = {r['id'] for r in store.list_items(tab='recommended', limit=100)['items']}
                    shown = set(page.locator('.news').evaluate_all('rows => rows.map(row => row.dataset.id)'))
                    assert shown == remaining, (len(shown), len(remaining))
                    # Returning to the top (upward scroll) must not mute unseen rows.
                    muted_before = {item_id for item_id in ids if store.item(item_id)['muted']}
                    top()
                    assert {item_id for item_id in ids if store.item(item_id)['muted']} == muted_before
                    page.reload()
                    expect(page.locator(f'article[data-id="{first}"]')).to_have_count(0)
                    page.locator('[data-tab=muted]').click()
                    restored = page.locator(f'article[data-id="{first}"]')
                    expect(restored).to_be_visible()
                    restored.locator('.mute-control').click()
                    expect(restored).to_have_count(0)
                    assert not store.item(first)['muted']
                    page.locator('[data-tab=all]').click()
                    expect(page.locator('#feed')).to_have_attribute('aria-busy', 'false')
                    expect(page.locator('.news').first).to_be_visible()
                    # Saved and read items are protected, even in the all view.
                    protected = page.locator('.news').first.get_attribute('data-id')
                    store.react(protected, {'saved': True})
                    page.reload()
                    page.locator('[data-tab=all]').click()
                    expect(page.locator('#feed')).to_have_attribute('aria-busy', 'false')
                    protected_row = page.locator(f'article[data-id="{protected}"]')
                    view(protected_row)
                    pass_row(protected_row)
                    assert not store.item(protected)['muted']
                    store.react(protected, {'saved': False, 'read': True})
                    page.reload()
                    page.locator('[data-tab=all]').click()
                    expect(page.locator('#feed')).to_have_attribute('aria-busy', 'false')
                    view(protected_row)
                    pass_row(protected_row)
                    assert not store.item(protected)['muted']
                    # Background scrolling under a modal cannot mute articles.
                    top()
                    row = page.locator('.news').nth(1)
                    view(row)
                    before_modal = {i for i in ids if store.item(i)['muted']}
                    page.locator('#settings-open').click()
                    page.evaluate('window.scrollBy(0, 1200)')
                    page.wait_for_timeout(250)
                    assert {i for i in ids if store.item(i)['muted']} == before_modal
                    page.locator('#settings-close').click()
                    # Failed writes leave persistence and pagination intact; no retry storm.
                    top()
                    candidate = next(i for i in page.locator('.news').evaluate_all('rows => rows.map(row => row.dataset.id)') if i != protected)
                    target = page.locator(f'article[data-id="{candidate}"]')
                    failures = []
                    endpoint = f'**/api/items/{candidate}/react'
                    def fail(route):
                        failures.append(route.request.post_data_json)
                        route.fulfill(status=503, json={'error': 'fixture failure'})
                    page.route(endpoint, fail)
                    view(target)
                    pass_row(target)
                    expect(page.locator('#notice')).to_contain_text('fixture failure')
                    assert not store.item(candidate)['muted']
                    page.evaluate('window.scrollBy(0, 10)')
                    page.wait_for_timeout(150)
                    assert failures == [{'muted': True}]
                    page.unroute(endpoint)
                    view(target)
                    pass_row(target)
                    expect(target).to_have_attribute('data-scroll-skipped', 'true')
                    assert store.item(candidate)['muted']
                    # OFF persists, including while using the same feed after a toggle.
                    top()
                    page.locator('#settings-open').click()
                    page.locator('#scroll-skip').uncheck()
                    page.locator('#settings-close').click()
                    page.reload()
                    expect(page.locator('.news').first).to_be_visible()
                    row = page.locator('.news').first
                    candidate = row.get_attribute('data-id')
                    view(row)
                    pass_row(row)
                    assert not store.item(candidate)['muted']
                    assert page.evaluate("localStorage.getItem('xangi-feed-scroll-skip')") == 'off'
                    assert not errors, errors
                    results.append({'width': width, 'result': 'passed', 'console_errors': errors})
                    context.close()
                # Browsers denying localStorage retain a usable session-only switch.
                context = browser.new_context(extra_http_headers={'Authorization': 'Bearer ' + 'fixture-' * 8})
                page = context.new_page()
                page.add_init_script("Object.defineProperty(window, 'localStorage', {get() {throw new Error('blocked')}})")
                page.goto(f'http://127.0.0.1:{server.server_port}')
                page.locator('#settings-open').click()
                page.locator('#scroll-skip').check()
                expect(page.locator('#scroll-skip-message')).to_contain_text('この画面でのみ有効')
                context.close()
                browser.close()
            (output / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
            print(json.dumps(results, ensure_ascii=False))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            server.collector.close()
            server.enricher.close()
            store.close()


if __name__ == '__main__':
    main()
