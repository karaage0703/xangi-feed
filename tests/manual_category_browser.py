"""Offline manual category flow, including RSS overlap and narrow screens."""
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
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
        for width in [1280, 390, 360]:
            with tempfile.TemporaryDirectory() as tmp:
                store = Store(Path(tmp) / 'fixture.db', seed=False)
                store.save_settings({'enabled': False})
                source = store.source(store.add_source('配信ブログ', 'https://example.org/feed', 'blog'))
                def entry(url, title):
                    return {'url': url, 'title': title, 'summary': '配信の抜粋', 'published': None, 'topics': []}
                entries = [entry('https://example.org/rss', '配信にもある記事'), entry('https://example.org/rss-only', '配信だけの記事')]
                store.ingest(source, entries)
                rss_id = next(i['id'] for i in store.list_items(tab='all')['items'] if i['url'] == entries[0]['url'])
                store.save_note(rss_id, '既存の記事のメモ')
                store.react(rss_id, {'read': True, 'rating': 1})
                server = Server(store, 'fixture')
                server.articles.fetcher = lambda _: '<html><title>個別の工作記事</title><article>追加した記事の本文です。</article></html>'.encode()
                worker = threading.Thread(target=server.serve_forever); worker.start()
                context = browser.new_context(viewport={'width': width, 'height': 900}, extra_http_headers={'Authorization': 'Bearer fixture'})
                page = context.new_page(); errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                try:
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    manual = page.locator('[data-tab=manual]')
                    manual.focus(); page.keyboard.press('Enter')
                    expect(manual).to_have_attribute('aria-pressed', 'true')
                    expect(page.locator('#feed')).to_contain_text('個別追加の記事が見つかりません')
                    expect(page.locator('#recommendation-panel')).not_to_be_visible()
                    expect(page.locator('#sort')).to_have_value('manual_added_at')
                    page.screenshot(path=str(output / f'empty-{width}.png'))
                    page.locator('#feed').get_by_role('button', name='記事を追加', exact=True).click()
                    expect(page.locator('#article-form [name=url]')).to_be_focused()
                    page.locator('#article-form [name=url]').fill('https://example.org/manual')
                    page.locator('#article-form button[type=submit]').click()
                    expect(page.locator('#detail')).to_be_visible()
                    expect(page.locator('#detail-body')).to_contain_text('個別の工作記事')
                    page.locator('#detail-close').click()
                    expect(page.locator('.news')).to_have_count(1)
                    expect(page.locator('.news .news-top')).to_contain_text('個別追加')
                    # An existing RSS item joins this category without duplication.
                    page.locator('#article-open').click()
                    page.locator('#article-form [name=url]').fill(entries[0]['url'])
                    page.locator('#article-form button[type=submit]').click()
                    expect(page.locator('#detail')).to_be_visible()
                    page.locator('#detail-close').click()
                    expect(page.locator('.news')).to_have_count(2)
                    expect(page.locator('.news').first).to_have_attribute('data-id', rss_id)
                    page.locator('[data-tab=all]').click()
                    page.locator('#sort').select_option('published')
                    manual.click()
                    expect(page.locator('#sort')).to_have_value('manual_added_at')
                    page.locator('#sort').select_option('updated')
                    page.reload(); manual.click()
                    expect(page.locator('#sort')).to_have_value('updated')
                    page.locator('#sort').select_option('manual_added_at')
                    page.locator('[data-tab=all]').click()
                    expect(page.locator('#sort')).to_have_value('published')
                    page.locator('[data-tab=read]').click()
                    expect(page.locator('#sort')).to_have_value('read_at')
                    manual.click()
                    expect(page.locator('#sort')).to_have_value('manual_added_at')

                    assert store.item(rss_id)['note'] == '既存の記事のメモ'
                    assert store.item(rss_id)['rating'] == 1 and store.item(rss_id)['read']
                    store.ingest(source, entries + [entry('https://example.org/manual', '個別の工作記事')])
                    video, _ = store.add_article({'url': 'https://youtu.be/fixture', 'title': '個別に追加した映像'})
                    store.save_article_content(video['id'], {'kind': 'video', 'body': '字幕の本文', 'content_kind': 'youtube_captions'})
                    page.reload(); manual.click()
                    expect(page.locator('.news')).to_have_count(3)
                    expect(page.locator('#sort')).to_have_value('manual_added_at')
                    expect(page.locator('.news').first).to_have_attribute('data-id', video['id'])
                    assert store.list_items(tab='all')['total'] == 4
                    if width < 900:
                        page.locator('#classification-open').click()
                    page.locator('#kind-choices [data-kind=video]').click()
                    if width < 900:
                        page.locator('#classification-done').click()
                    expect(page.locator('.news')).to_have_count(1)
                    expect(page.locator('.news-title')).to_have_text('個別に追加した映像')
                    page.locator('#active-filters').get_by_role('button', name='すべて解除', exact=True).click()
                    expect(page.locator('.news')).to_have_count(3)
                    page.locator('#search').fill('既存の記事のメモ')
                    expect(page.locator('.news')).to_have_count(1)
                    expect(page.locator('.news-title')).to_have_text('配信にもある記事')
                    page.locator('#search').fill('')
                    expect(page.locator('.news')).to_have_count(3)
                    page.locator('[data-tab=all]').click()
                    expect(page.locator('.news')).to_have_count(4)
                    manual.click(); expect(page.locator('.news')).to_have_count(3)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'page overflow'
                    assert not errors, errors
                    page.screenshot(path=str(output / f'category-{width}.png'))
                    results.append({'width': width, 'empty_and_add': True, 'keyboard_navigation': True, 'rss_duplicate': True, 'rss_refresh_and_reload': True, 'read_and_notes_preserved': True, 'added_order_and_independent_sort_persistence': True, 'kind_and_search': True, 'page_errors': errors})
                finally:
                    context.close(); server.shutdown(); server.server_close(); worker.join(); store.close()
        browser.close()
    (output / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    main()
