"""Category navigation resets scroll; refreshing and reading keep their position."""
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
    results = []
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / 'fixture.db', seed=False)
        store.save_settings({'enabled': False})
        for i in range(65):
            item, _ = store.add_article({'url': f'https://example.org/{i}', 'title': f'記事 {i}'})
            store.react(item['id'], {'saved': True})
        server = Server(store, 'fixture')
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=os.environ.get('CHROMIUM_EXECUTABLE'))
                for width in [1280, 393, 360]:
                    context = browser.new_context(viewport={'width': width, 'height': 800}, extra_http_headers={'Authorization': 'Bearer fixture'})
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(page.locator('.news')).to_have_count(50)

                    def choose(view):
                        if width < 901:
                            page.locator('#classification-open').evaluate('(el) => el.focus({preventScroll: true})')
                            page.locator('#classification-open').press('Enter')
                        page.locator(f'button[data-custom-filter="{view}"]').click()
                        page.wait_for_function('() => !customFilterBusy && !state.loading')
                        if width < 901:
                            expect(page.locator('#classification-dialog')).not_to_be_visible()

                    def down():
                        page.evaluate('window.scrollTo(0, 1400)')
                        page.wait_for_function('() => window.scrollY > 1000')

                    choose('preset-all')
                    down()
                    before = page.evaluate('window.scrollY')
                    choose('preset-saved')
                    expect(page.locator('#current-view-title')).to_have_text('ブックマーク')
                    expect(page.locator('.news')).to_have_count(50)
                    after = page.evaluate('window.scrollY')
                    page.screenshot(path=str(output / f'{width}-switch.png'))
                    assert after == 0, f'{width}: category switch kept scroll {before} -> {after}'

                    down()
                    choose('preset-all')
                    assert page.evaluate('window.scrollY') == 0, 'returning category must start at top'
                    down()
                    choose('preset-all')
                    assert page.evaluate('window.scrollY') > 1000, 'same category keeps reading position'

                    # A failed article request must not force a jump to the top.
                    page.route('**/api/items?*', lambda route: route.fulfill(status=503, json={'error': 'fixture failure'}))
                    choose('preset-saved')
                    assert page.evaluate('window.scrollY') > 1000, 'failed load must not reset scroll'
                    page.unroute('**/api/items?*')
                    choose('preset-all')

                    # Appending the second page keeps the viewport at the bottom.
                    page.locator('#more').click()
                    expect(page.locator('.news')).to_have_count(65)
                    assert page.evaluate('window.scrollY') > 1000, 'pagination must not reset scroll'
                    row = page.locator('.news').nth(8)
                    row.scroll_into_view_if_needed()
                    before_reaction = row.evaluate('(el) => el.getBoundingClientRect().top')
                    control = row.locator('.bookmark-control')
                    control.click()
                    expect(control).to_have_attribute('aria-pressed', 'false')
                    expect(control).to_be_enabled()
                    assert abs(row.evaluate('(el) => el.getBoundingClientRect().top') - before_reaction) < 2
                    control.click()
                    expect(control).to_have_attribute('aria-pressed', 'true')
                    expect(control).to_be_enabled()
                    assert not errors, errors
                    results.append({'width': width, 'before': before, 'after': after, 'same_category': 'kept', 'failure': 'kept', 'pagination': 'kept', 'reaction': 'kept'})
                    context.close()
                browser.close()
        finally:
            server.shutdown()
            worker.join()
            server.server_close()
            store.db.close()
    (output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    main()
