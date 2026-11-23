"""Verify that article details stay responsive while requests are in flight."""
import os
import sys
import tempfile
from pathlib import Path
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect, sync_playwright

from xangi_feed.server import Server
from xangi_feed.store import Store


def main():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / 'feed.db', seed=False)
        store.save_settings({'enabled': False, 'show_link_previews': False})
        source = store.source(store.add_source('配信元', 'https://example.org/feed', 'blog'))
        store.ingest(source, [
            dict(url=f'https://example.org/{i}', title=f'記事{i}', summary=f'抜粋{i}',
                 topics=[], published=1790000000 + i)
            for i in range(2)
        ])
        ids = {item['title']: item['id'] for item in store.list_items(tab='all')['items']}
        server = Server(store, 'fixture')
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
                page = browser.new_page(viewport={'width': 1440, 'height': 900},
                                        extra_http_headers={'Authorization': 'Bearer fixture'})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.add_init_script(r"""
                    localStorage.setItem('xangi-feed-desktop-reader', 'on');
                    const originalFetch = window.fetch;
                    window.blockedDetails = {};
                    window.failedDetails = [];
                    window.completedDetails = 0;
                    window.fetch = async (url, ...args) => {
                      const id = new URL(url).pathname.match(/\/api\/items\/([0-9a-f]{24})$/)?.[1];
                      if (!id) return originalFetch(url, ...args);
                      if (Object.hasOwn(blockedDetails, id)) {
                        await new Promise(resolve => blockedDetails[id] = resolve);
                      }
                      const response = failedDetails.includes(id)
                        ? new Response('{"error":"fixture"}', {status: 503})
                        : await originalFetch(url, ...args);
                      const json = response.json.bind(response);
                      response.json = async () => {
                        const value = await json();
                        requestAnimationFrame(() => window.completedDetails++);
                        return value;
                      };
                      return response;
                    };
                """)
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                page.locator('[data-tab=all]').click()
                expect(page.locator('.news-title')).to_have_count(2)
                dialog = page.locator('#detail')
                title = dialog.locator('.detail-title')
                older = page.get_by_role('link', name='記事0', exact=True)
                newer = page.get_by_role('link', name='記事1', exact=True)
                older_id, newer_id = ids['記事0'], ids['記事1']

                def hold(item_id):
                    page.evaluate('(id) => window.blockedDetails[id] = null', item_id)

                def release(item_id):
                    page.evaluate('id => { const resume = blockedDetails[id]; delete blockedDetails[id]; resume(); }',
                                  item_id)

                hold(older_id)
                older.click()
                expect(title).to_have_text('記事0', timeout=500)
                expect(dialog.locator('.excerpt')).to_have_text('抜粋0')
                expect(older).to_have_attribute('aria-current', 'true')
                expect(older).to_be_focused()
                expect(dialog.locator('#article-note')).to_have_count(0)

                store.save_note(newer_id, 'サーバー側の最新メモ')
                newer.click()
                expect(title).to_have_text('記事1')
                expect(dialog.locator('#article-note')).to_have_value('サーバー側の最新メモ')
                release(older_id)
                page.wait_for_function('() => window.completedDetails === 2')
                expect(title).to_have_text('記事1')
                expect(newer).to_have_attribute('aria-current', 'true')

                hold(older_id)
                older.click()
                expect(title).to_have_text('記事0', timeout=500)
                dialog.locator('#detail-close').click()
                release(older_id)
                page.wait_for_function('() => window.completedDetails === 3')
                expect(dialog).to_be_hidden()

                page.evaluate('id => window.failedDetails.push(id)', older_id)
                older.click()
                expect(dialog.get_by_role('status')).to_contain_text('詳細を読み込めませんでした')
                expect(dialog.locator('.excerpt')).to_have_text('抜粋0')
                page.evaluate('window.failedDetails = []')
                older.click()
                expect(dialog.locator('#article-note')).to_be_visible()

                dialog.locator('#detail-close').click()
                page.locator('#reader-toggle').click()
                expect(page.locator('#reader-toggle')).to_have_attribute('aria-pressed', 'false')
                for width, display in [(1440, 'summary'), (390, 'summary'), (390, 'titles')]:
                    page.set_viewport_size({'width': width, 'height': 900})
                    page.locator('#list-display').select_option(display)
                    detail_button = page.locator(f'.news[data-id="{older_id}"]').get_by_role('button', name='記事の詳細')
                    hold(older_id)
                    detail_button.click()
                    expect(dialog).to_be_visible(timeout=1000)
                    expect(title).to_have_text('記事0', timeout=1000)
                    assert dialog.evaluate("(dialog) => dialog.matches(':modal')")
                    expect(dialog.locator('.excerpt')).to_have_text('抜粋0')
                    expect(dialog.get_by_role('status')).to_have_text('詳細を読み込み中…')
                    expect(dialog.locator('#article-note')).to_have_count(0)
                    release(older_id)
                    expect(dialog.locator('#article-note')).to_be_visible()
                    expect(dialog.locator('.detail-loading')).to_have_count(0)
                    dialog.locator('#detail-close').click()
                    expect(detail_button).to_be_focused()

                hold(older_id)
                detail_button.click()
                expect(dialog).to_be_visible(timeout=1000)
                expect(title).to_have_text('記事0', timeout=1000)
                completed = page.evaluate('completedDetails')
                page.keyboard.press('Escape')
                expect(dialog).to_be_hidden()
                expect(detail_button).to_be_enabled()
                expect(detail_button).to_be_focused()
                release(older_id)
                page.wait_for_function('count => completedDetails > count', arg=completed)
                expect(dialog).to_be_hidden()

                page.evaluate('id => failedDetails.push(id)', older_id)
                detail_button.click()
                expect(dialog.get_by_role('status')).to_have_text('詳細を読み込めませんでした。')
                expect(dialog.locator('.excerpt')).to_have_text('抜粋0')
                page.evaluate('window.failedDetails = []')
                dialog.get_by_role('button', name='再試行').click()
                expect(dialog.locator('#article-note')).to_be_visible()
                assert not errors, errors
                assert not store.item(older_id)['read'] and not store.item(newer_id)['read']
                browser.close()
                print('Passed: three-pane reader and desktop/mobile detail dialog, pending responses, focus, cancellation, retry')
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
            server.collector.close()
            server.enricher.close()
            store.close()


if __name__ == '__main__':
    main()
