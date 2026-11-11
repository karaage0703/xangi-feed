"""Sidebar resize and reading flow; fixture-only Playwright browser checks."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, os.environ.get('FEED_TEST_REPO', str(Path(__file__).resolve().parent.parent)))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / 'fixture.db', seed=False)
        source = store.source(store.add_source('工作ブログ', 'https://example.org/feed', 'blog'))
        store.ingest(source, [{'url': 'https://example.org/article', 'title': 'ロボット工作の記事', 'summary': '表示確認用の記事', 'topics': ['ロボット'], 'published': None}])
        server = Server(store, 'fixture-token-' * 4)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1280, 'height': 900}, extra_http_headers={'Authorization': 'Bearer ' + 'fixture-token-' * 4})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                url = f'http://127.0.0.1:{server.server_port}'
                page.goto(url)
                expect(page.locator('.news')).to_have_count(1)
                handle = page.get_by_role('separator', name='分類パネルの幅')
                expect(handle).to_be_visible()
                expect(handle).to_have_attribute('aria-valuenow', '240')
                page.screenshot(path=str(output / 'desktop-default.png'), full_page=True)

                def drag(delta, cancel=False):
                    box = handle.bounding_box()
                    page.mouse.move(box['x'] + box['width'] / 2, box['y'] + 40)
                    page.mouse.down()
                    page.mouse.move(box['x'] + box['width'] / 2 + delta, box['y'] + 40, steps=5)
                    if cancel:
                        page.keyboard.press('Escape')
                    page.mouse.up()

                drag(120)
                expect(handle).to_have_attribute('aria-valuenow', '360')
                assert page.locator('#classification').bounding_box()['width'] == 360
                page.reload()
                expect(handle).to_have_attribute('aria-valuenow', '360')
                drag(60, cancel=True)
                expect(handle).to_have_attribute('aria-valuenow', '360')
                assert page.evaluate("localStorage.getItem('xangi-feed-classification-width')") == '360'
                handle.focus()
                page.keyboard.press('Shift+ArrowRight')
                expect(handle).to_have_attribute('aria-valuenow', '400')
                page.keyboard.press('ArrowLeft')
                expect(handle).to_have_attribute('aria-valuenow', '390')
                page.keyboard.press('Home')
                expect(handle).to_have_attribute('aria-valuenow', '180')
                page.keyboard.press('End')
                expect(handle).to_have_attribute('aria-valuenow', '480')
                drag(2000)
                expect(handle).to_have_attribute('aria-valuenow', '480')
                page.screenshot(path=str(output / 'desktop-expanded.png'), full_page=True)
                page.set_viewport_size({'width': 901, 'height': 900})
                expect(handle).to_have_attribute('aria-valuenow', '457')
                assert page.locator('main.shell').bounding_box()['width'] >= 360
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.set_viewport_size({'width': 1280, 'height': 900})
                expect(handle).to_have_attribute('aria-valuenow', '480')
                handle.dblclick()
                expect(handle).to_have_attribute('aria-valuenow', '240')
                page.reload()
                expect(handle).to_have_attribute('aria-valuenow', '240')
                for width in [900, 393, 360]:
                    page.set_viewport_size({'width': width, 'height': 900})
                    expect(handle).not_to_be_visible()
                    page.locator('#classification-open').click()
                    page.locator('#kind-choices [data-kind=blog]').click()
                    page.locator('#classification-done').click()
                    expect(page.locator('.news')).to_have_count(1)
                    page.locator('.more-actions summary').click()
                    page.get_by_role('button', name='記事の詳細', exact=True).click()
                    expect(page.locator('#detail')).to_be_visible()
                    page.locator('#detail-close').click()
                    expect(page.locator('#detail')).not_to_be_visible()
                    page.screenshot(path=str(output / f'mobile-{width}.png'), full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.set_viewport_size({'width': 1280, 'height': 900})
                expect(handle).to_have_attribute('aria-valuenow', '240')
                page.evaluate("localStorage.setItem('xangi-feed-classification-width', 'invalid')")
                page.reload()
                expect(handle).to_have_attribute('aria-valuenow', '240')
                page.add_init_script("Object.defineProperty(window, 'localStorage', {get() {throw new Error('blocked')}})")
                page.reload()
                handle.focus()
                page.keyboard.press('ArrowRight')
                expect(handle).to_have_attribute('aria-valuenow', '250')
                assert not errors, errors
                browser.close()
            result = {'result': 'passed', 'drag_reload_cancel_keyboard_limits_reset': True, 'responsive_saved_width': True, 'mobile_filter_detail_return': [900, 393, 360], 'invalid_or_blocked_storage': True, 'console_errors': errors}
            (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps(result, ensure_ascii=False))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            server.collector.close()
            server.enricher.close()
            store.close()


if __name__ == '__main__':
    main()
