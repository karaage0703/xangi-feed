"""Fixture-only custom tabs, server order, and isolated-computer checks."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store, RECOMMENDATION_DEFAULTS


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / 'test.db', seed=False)
        for title, kind in [('Podcast fixture', 'podcast'), ('Article fixture', 'rss')]:
            item, _ = store.add_article({'url': 'https://example.org/' + kind, 'title': title})
            with store.transaction() as db: db.execute('UPDATE items SET kind=? WHERE id=?', (kind, item['id']))
        server = Server(store, 'fixture-token')
        worker = threading.Thread(target=server.serve_forever); worker.start()
        checks = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE', '/usr/bin/chromium-browser'))
                for width in [1280, 393, 360]:
                    for saved in store.custom_filters(): store.delete_custom_filter(saved['id'])
                    with store.transaction() as db: db.execute("DELETE FROM metadata WHERE key='custom-default-classification'")
                    context = browser.new_context(viewport={'width': width, 'height': 1000}, extra_http_headers={'Authorization': 'Bearer fixture-token'})
                    page = context.new_page(); errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(page.locator('#custom-filter-plus')).to_be_enabled()
                    expect(page.locator('.news')).to_have_count(2)
                    if width < 800: page.locator('#classification-open').click()
                    page.locator('#kind-choices [data-kind=podcast]').click()
                    if width < 800: page.locator('#classification-close').click()
                    expect(page.locator('.news')).to_have_count(1)
                    def add_tab(name):
                        page.locator('#custom-filter-plus').focus()
                        page.locator('#custom-filter-plus').press('Enter')
                        expect(page.locator('#custom-filter-new-name')).to_be_focused()
                        page.locator('#custom-filter-new-name').fill(name)
                        page.locator('#custom-filter-new-name').press('Enter')
                        expect(page.locator('#custom-filter-new')).not_to_be_visible()
                    add_tab('ポッドキャストだけ')
                    tabs = page.locator('#custom-filter-tabs button')
                    expect(tabs).to_have_text(['ポッドキャストだけ'])
                    expect(tabs.first).to_have_attribute('aria-pressed', 'true')
                    expect(page.locator('[data-tab=recommended]')).to_have_attribute('aria-pressed', 'false')
                    podcast_id = tabs.first.get_attribute('data-custom-filter')
                    page.locator('#recommendation-toggle summary').click()
                    page.locator('[name=recommendation_min_preference]').fill('100')
                    add_tab('好みが高い記事')
                    expect(tabs).to_have_text(['ポッドキャストだけ', '好みが高い記事'])
                    expect(page.locator('.news')).to_have_count(0)
                    high_id = tabs.nth(1).get_attribute('data-custom-filter')
                    page.locator('#custom-filter-editor summary').click()
                    page.locator('#custom-filter-left').click()
                    expect(tabs).to_have_text(['好みが高い記事', 'ポッドキャストだけ'])
                    expect(page.locator('#custom-filter-left')).to_be_disabled()
                    # Failed order saves retain the prior order and can be retried.
                    page.route('**/api/custom-filters/reorder', lambda route: route.fulfill(status=503, content_type='application/json', body='{"error":"fixture reorder failure"}'))
                    page.locator('#custom-filter-right').click()
                    expect(page.locator('#custom-filter-message')).to_contain_text('並べ替えを保存できませんでした')
                    expect(tabs).to_have_text(['好みが高い記事', 'ポッドキャストだけ'])
                    page.unroute('**/api/custom-filters/reorder')
                    page.locator('#custom-filter-right').click()
                    expect(tabs).to_have_text(['ポッドキャストだけ', '好みが高い記事'])
                    page.locator('#custom-filter-left').click()
                    expect(tabs).to_have_text(['好みが高い記事', 'ポッドキャストだけ'])
                    if width == 1280:
                        tabs.first.drag_to(tabs.nth(1))
                        expect(tabs).to_have_text(['ポッドキャストだけ', '好みが高い記事'])
                        page.locator('#custom-filter-left').click()
                        expect(tabs).to_have_text(['好みが高い記事', 'ポッドキャストだけ'])
                    page.locator(f'[data-custom-filter="{podcast_id}"]').click()
                    expect(page.locator('.news')).to_have_count(1)
                    expect(page.locator('[name=recommendation_min_preference]')).to_have_value('')
                    page.locator('#custom-filter-name').fill('Podcast更新')
                    page.locator('#custom-filter-update').click()
                    expect(tabs).to_have_text(['好みが高い記事', 'Podcast更新'])
                    other = browser.new_context(viewport={'width': width, 'height': 1000}, extra_http_headers={'Authorization': 'Bearer fixture-token'})
                    second = other.new_page(); second.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(second.locator('#custom-filter-tabs button')).to_have_text(['好みが高い記事', 'Podcast更新'])
                    second.locator('[data-tab=all]').click()
                    second.locator(f'[data-custom-filter="{podcast_id}"]').click()
                    expect(second.locator('.news')).to_have_count(1)
                    expect(second.locator('.news')).to_contain_text('Podcast fixture')
                    second.locator('[data-tab=all]').click()
                    expect(second.locator('.news')).to_have_count(2)
                    second.locator(f'[data-custom-filter="{high_id}"]').click()
                    expect(second.locator('.news')).to_have_count(0)
                    second.locator('#custom-filter-editor summary').click()
                    second.on('dialog', lambda dialog: dialog.accept())
                    second.locator('#custom-filter-delete').click()
                    expect(second.locator('#custom-filter-tabs button')).to_have_text(['Podcast更新'])
                    second.locator(f'[data-custom-filter="{podcast_id}"]').click()
                    expect(second.locator('.news')).to_have_count(1)
                    # Max count: native horizontal scrolling keeps the + reachable.
                    for index in range(99):
                        store.save_custom_filter({'name': f'条件タブ{index}', 'recommendations': dict(RECOMMENDATION_DEFAULTS)})
                    second.reload()
                    expect(second.locator('#custom-filter-tabs button')).to_have_count(100)
                    second.locator('#custom-filter-tabs button').last.click()
                    expect(second.locator('#custom-filter-tabs button').last).to_have_attribute('aria-pressed', 'true')
                    second.locator('#custom-filter-plus').click()
                    expect(second.locator('#custom-filter-new')).to_be_visible()
                    second.locator('#custom-filter-new-cancel').click()
                    assert second.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                    # Keep evidence legible by capturing a representative two-tab view.
                    for saved in store.custom_filters()[2:]: store.delete_custom_filter(saved['id'])
                    second.reload()
                    second.locator(f'[data-custom-filter="{podcast_id}"]').click()
                    second.screenshot(path=str(output / f'tabs-{width}.png'), full_page=True)
                    assert not errors, errors
                    checks.append({'width': width, 'add_select_reorder_cross_context_delete_100_tabs': 'passed'})
                    other.close(); context.close()
                browser.close()
            (output / 'browser-results.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2))
        finally:
            server.shutdown(); server.server_close(); worker.join(); store.close()

if __name__ == '__main__': main()
