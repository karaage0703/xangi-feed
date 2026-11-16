"""Keep read and skipped cards actionable until explicit navigation or reload."""
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
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ.get('CHROMIUM_EXECUTABLE'))
        for width in [1280, 393, 360]:
            with tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory) / 'fixture.db', seed=False)
                store.save_settings({'enabled': False})
                for i in range(65):
                    store.add_article({'url': f'https://example.org/{i}', 'title': f'記事 {i}'})
                server = Server(store, 'fixture')
                worker = threading.Thread(target=server.serve_forever)
                worker.start()
                context = browser.new_context(viewport={'width': width, 'height': 800}, extra_http_headers={'Authorization': 'Bearer fixture'})
                context.route('https://example.org/**', lambda route: route.fulfill(content_type='text/html', body='<p>Fixture article</p>'))
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                def choose(view):
                    if width < 901:
                        page.locator('#classification-open').evaluate('(el) => el.focus({preventScroll: true})')
                        page.locator('#classification-open').press('Enter')
                    page.locator(f'button[data-custom-filter="{view}"]').click()
                    page.wait_for_function('() => !state.loading && !customFilterBusy')
                try:
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(page.locator('.news')).to_have_count(50)
                    row = page.locator('.news').first
                    read_id = row.get_attribute('data-id')
                    read_row = page.locator(f'.news[data-id="{read_id}"]')
                    row.locator('.read-control').click()
                    expect(read_row).to_have_count(1)
                    expect(read_row.locator('.read-control')).to_have_attribute('aria-pressed', 'true')
                    expect(read_row.locator('.read-control')).to_be_enabled()
                    assert store.item(read_id)['read']
                    read_row.locator('.rating-control').first.click()
                    expect(read_row.locator('.rating-control').first).to_have_attribute('aria-pressed', 'true')
                    assert store.item(read_id)['rating'] == 1
                    expect(page.locator('.news')).to_have_count(50)

                    opened_row = page.locator('.news').nth(3)
                    opened_id = opened_row.get_attribute('data-id')
                    with context.expect_page() as popup:
                        opened_row.locator('.news-title').click()
                    popup.value.close()
                    expect(opened_row.locator('.read-control')).to_have_attribute('aria-pressed', 'true')
                    assert store.item(opened_id)['read']
                    opened_row.locator('.rating-control').first.click()
                    expect(opened_row.locator('.rating-control').first).to_have_attribute('aria-pressed', 'true')

                    # Automatic save/analysis updates must retain the current cards.
                    page.evaluate('() => { state.saveRevision = -1; state.analysisRevision = -1; return poll(); }')
                    expect(read_row).to_have_count(1)

                    page.locator('#settings-open').click()
                    page.locator('#scroll-skip').check()
                    page.locator('#settings-close').click()
                    skipped = page.locator('.news').nth(2)
                    skipped_id = skipped.get_attribute('data-id')
                    skipped.scroll_into_view_if_needed()
                    page.wait_for_timeout(200)
                    page.evaluate('(el) => scrollBy(0, el.getBoundingClientRect().bottom + 20)', skipped.element_handle())
                    expect(skipped).to_have_attribute('data-scroll-skipped', 'true')
                    assert store.item(skipped_id)['muted']
                    # Disable further automatic skipping while checking retained cards.
                    page.locator('#settings-open').click()
                    page.locator('#scroll-skip').uncheck()
                    page.locator('#settings-close').click()
                    skipped.locator('.rating-control').first.click()
                    expect(skipped.locator('.rating-control').first).to_have_attribute('aria-pressed', 'true')
                    assert store.item(skipped_id)['rating'] == 1
                    expect(read_row).to_have_count(1)

                    page.locator('#more').click()
                    expect(page.locator('.news')).to_have_count(65)
                    ids = page.locator('.news').evaluate_all('(rows) => rows.map(row => row.dataset.id)')
                    assert len(set(ids)) == 65, 'No skipped or duplicate pages after read/skip/Good'
                    expect(read_row).to_have_count(1)
                    expect(skipped).to_have_count(1)
                    page.screenshot(path=str(out / f'{width}-retained.png'))

                    # Explicit navigation applies the persisted state.
                    choose('preset-all')
                    choose('preset-recommended')
                    expect(read_row).to_have_count(0)
                    expect(page.locator(f'.news[data-id="{opened_id}"]')).to_have_count(0)
                    expect(page.locator(f'.news[data-id="{skipped_id}"]')).to_have_count(0)
                    fresh = page.locator('.news').first
                    fresh_id = fresh.get_attribute('data-id')
                    fresh.locator('.read-control').click()
                    expect(fresh.locator('.read-control')).to_be_enabled()
                    expect(fresh).to_have_count(1)
                    page.reload()
                    expect(page.locator('#feed')).to_have_attribute('aria-busy', 'false')
                    expect(page.locator(f'.news[data-id="{fresh_id}"]')).to_have_count(0)
                    assert not errors, errors
                    results.append({'width': width, 'read_then_good': True, 'link_open_then_good': True, 'skip_then_good': True, 'automatic_updates': True, 'paging_unique': 65, 'navigation_and_reload': True})
                finally:
                    context.close()
                    server.shutdown()
                    worker.join()
                    server.server_close()
                    store.close()
        browser.close()
    (out / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results))


if __name__ == '__main__':
    main()
