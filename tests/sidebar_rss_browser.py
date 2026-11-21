"""Exercise RSS navigation and saved-view isolation with a local fixture."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import RECOMMENDATION_DEFAULTS, VIEW_DEFAULTS, Store


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / 'fixture.db', seed=False)
        store.save_settings({'enabled': False, 'show_link_previews': False})
        sources = {}
        for name, slug, kind, size in [
            ('工作ブログ', 'maker', 'blog', 53),
            ('実験ノート', 'lab', 'blog', 1),
            ('科学ニュース', 'science', 'news', 1),
            ('取得前のブログ', 'empty', 'blog', 0),
        ]:
            source = store.source(store.add_source(name, f'https://example.org/feed/{slug}', kind))
            sources[slug] = source
            store.ingest(source, [
                {'url': f'https://example.org/{slug}/{n}', 'title': f'{name} {n}', 'summary': '検証用',
                 'topics': [], 'published': None}
                for n in range(1, size + 1)
            ])
        read = next(item for item in store.list_items('all')['items'] if item['title'] == '工作ブログ 1')
        store.react(read['id'], {'read': True})
        view = store.save_custom_filter({
            'name': '未読のブログ', 'kinds': ['blog'], 'recommendations': dict(RECOMMENDATION_DEFAULTS),
            'conditions': {**VIEW_DEFAULTS, 'read': 'unread'},
        })
        server = Server(store, 'fixture')
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE'))
                for width in (1280, 393, 360):
                    mobile = width <= 900
                    context = browser.new_context(
                        viewport={'width': width, 'height': 850}, has_touch=mobile, is_mobile=mobile,
                        extra_http_headers={'Authorization': 'Bearer fixture'},
                    )
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    category = page.locator(f'[data-custom-filter="{view["id"]}"]')
                    expect(category).to_be_enabled()
                    dialog = page.locator('#classification-dialog')

                    def open_menu():
                        if mobile:
                            page.locator('#classification-open').click()
                            expect(dialog).to_be_visible()

                    def wait_articles(count):
                        expect(page.locator('.news')).to_have_count(count)
                        expect(page.locator('#feed')).to_have_attribute('aria-busy', 'false')
                        if mobile:
                            expect(dialog).not_to_be_visible()

                    open_menu()
                    rss = page.locator('#rss-menu > summary')
                    top = page.locator('#rss-sources')
                    expect(top).not_to_be_visible()
                    rss.press('Enter')
                    expect(top.locator('button')).to_have_count(4)
                    expect(top.get_by_text('取得前のブログ')).to_be_visible()
                    page.screenshot(path=str(output / f'{width}-all-rss.png'))
                    rss.click()
                    expect(top.get_by_text('工作ブログ')).not_to_be_visible()
                    rss.click()
                    top.get_by_text('工作ブログ').click()
                    wait_articles(50)
                    expect(page.locator('#current-view-title')).to_have_text('工作ブログ')
                    expect(page.locator('#view-filter-open')).to_be_disabled()
                    page.locator('#more').click()
                    wait_articles(53)
                    expect(page.locator('#feed').get_by_role('link', name='工作ブログ 1', exact=True)).to_have_count(1)
                    assert '実験ノート' not in page.locator('#feed').inner_text()

                    open_menu()
                    rss.click()
                    toggle = page.get_by_role('button', name='未読のブログのRSS配信元')
                    toggle.press('Enter')
                    expect(toggle).to_have_attribute('aria-expanded', 'true')
                    nested = page.locator(f'#view-rss-{view["id"]}')
                    expect(nested.get_by_role('button')).to_have_text(['工作ブログ', '実験ノート'])
                    page.screenshot(path=str(output / f'{width}-sidebar.png'))
                    nested.get_by_text('工作ブログ').click()
                    wait_articles(50)
                    expect(page.locator('#current-view-title')).to_have_text('未読のブログ / 工作ブログ')
                    expect(page.locator('#view-filter-open')).to_be_enabled()
                    page.locator('#more').click()
                    wait_articles(52)
                    expect(page.locator('#feed').get_by_role('link', name='工作ブログ 1', exact=True)).to_have_count(0)
                    assert '実験ノート' not in page.locator('#feed').inner_text()

                    open_menu()
                    expect(nested.get_by_text('工作ブログ')).to_have_attribute('aria-pressed', 'true')
                    toggle.click()
                    expect(toggle).to_have_attribute('aria-expanded', 'false')
                    expect(nested).not_to_be_visible()
                    category.click()
                    wait_articles(50)
                    expect(page.locator('#current-view-title')).to_have_text('未読のブログ')
                    page.locator('#more').click()
                    wait_articles(53)
                    assert '実験ノート' in page.locator('#feed').inner_text()
                    assert store.custom_filter(view['id']) == view
                    assert not errors, errors
                    results.append({'width': width, 'all_source_articles': 53, 'nested_unread_articles': 52,
                                    'category_articles': 53, 'saved_view_unchanged': True})
                    context.close()
                browser.close()
        finally:
            server.shutdown()
            worker.join()
            server.server_close()
            store.close()
    (output / 'browser-results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results))


if __name__ == '__main__':
    main()
