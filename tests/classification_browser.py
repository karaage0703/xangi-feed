"""Classification flow with fixture data only; requires installed Playwright."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright, expect
from xangi_feed.server import Server
from xangi_feed.store import Store


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / 'fixture.db', seed=False)
        definitions = [('工作ブログ', 'blog', ['ロボット']), ('映像チャンネル', 'video', ['ローカルAI']), ('研究メモ', 'blog', ['ローカルAI'])]
        sources = []
        for i, (name, kind, tags) in enumerate(definitions):
            # Fixture RSS source; assigning video here avoids channel-resolution networking.
            source = store.source(store.add_source(name, f'https://example.org/rss/{i}', 'blog'))
            with store.transaction() as db:
                db.execute('UPDATE sources SET kind=? WHERE id=?', (kind, source['id']))
            source = store.source(source['id']); sources.append(source)
            store.ingest(source, [{'url': f'https://example.org/{i}', 'title': name + 'の記事', 'summary': '表示検証用', 'topics': tags, 'published': None}])
        for i in range(747):
            store.add_source(f'購読チャンネル {i:03}', f'https://example.org/extra/{i}', 'blog')
        server = Server(store, 'fixture-token-' * 4)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        try:
            results = []
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
                for label, width in [('desktop', 1280), ('mobile', 393), ('small-mobile', 360)]:
                    page = browser.new_page(viewport={'width': width, 'height': 900}, extra_http_headers={'Authorization': 'Bearer ' + 'fixture-token-' * 4})
                    errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(page.locator('.news')).to_have_count(3)
                    mobile = width <= 900
                    toggle = page.locator('#recommendation-toggle')
                    expect(toggle).not_to_have_attribute('open', '')
                    form = page.locator('#recommendation-form')
                    expect(form.locator('[name=recommendation_min_preference]')).not_to_be_visible()
                    toggle.locator('summary').click()
                    for name in ['recommendation_min_preference', 'recommendation_priority', 'recommendation_days', 'recommendation_hide_bad', 'recommendation_mix_types']:
                        expect(form.locator(f'[name={name}]')).to_be_visible()
                    form.locator('[name=recommendation_days]').fill('0'); form.locator('[name=recommendation_days]').press('Tab')
                    expect(page.locator('#recommendation-message')).to_have_text('保存して反映しました。')
                    page.screenshot(path=str(output / f'{label}-custom-open.png'), full_page=True)
                    page.reload()
                    expect(form.locator('[name=recommendation_min_preference]')).not_to_be_visible()
                    toggle.locator('summary').click()
                    expect(form.locator('[name=recommendation_days]')).to_have_value('0')
                    toggle.locator('summary').click()
                    response = page.request.get(f'http://127.0.0.1:{server.server_port}/api/items?tab=all&kind=blog&kind=video&limit=1')
                    assert response.ok and response.json()['total'] == 3 and len(response.json()['items']) == 1
                    if mobile:
                        expect(page.locator('#classification')).not_to_be_visible()
                        page.locator('#classification-open').click()
                        page.keyboard.press('Escape')
                        expect(page.locator('#classification-open')).to_be_focused()
                        page.locator('#classification-open').click()
                    else:
                        expect(page.locator('#classification')).to_be_visible()
                    page.locator('#kind-choices [data-kind=blog]').click()
                    expect(page.locator('.news')).to_have_count(2)
                    page.locator('#kind-choices [data-kind=video]').click()
                    expect(page.locator('.news')).to_have_count(3)
                    expect(page.locator('#kind-choices [data-kind=blog]')).to_have_attribute('aria-pressed', 'true')
                    expect(page.locator('#kind-choices [data-kind=video]')).to_have_attribute('aria-pressed', 'true')
                    if mobile:
                        page.locator('#classification-done').click()
                    page.get_by_role('button', name='ブログの絞り込みを解除', exact=True).click()
                    expect(page.locator('.news')).to_have_count(1)
                    if mobile:
                        page.locator('#classification-open').click()
                    page.locator('#kind-choices [data-kind=video]').click()
                    expect(page.locator('.news')).to_have_count(3)
                    page.locator('#kind-choices [data-kind=blog]').click()
                    expect(page.locator('.news')).to_have_count(2)
                    page.locator('#topic-choices [data-topic="ロボット"]').click()
                    expect(page.locator('.news')).to_have_count(1)
                    page.locator('#topic-choices [data-topic="ローカルAI"]').click()
                    expect(page.locator('.news')).to_have_count(2)
                    page.locator('#source-search').fill('研究')
                    expect(page.locator('#source-match')).to_have_text('1件の配信元')
                    expect(page.locator('#source option')).to_have_count(2)
                    page.locator('#source').select_option(str(sources[2]['id']))
                    expect(page.locator('.news')).to_have_count(1)
                    page.screenshot(path=str(output / f'{label}-selection.png'), full_page=True)
                    if mobile:
                        page.locator('#classification-done').click()
                        expect(page.locator('#classification-dialog')).not_to_be_visible()
                    expect(page.locator('#active-filters')).to_contain_text('研究メモ')
                    page.get_by_role('button', name='ロボットの絞り込みを解除', exact=True).click()
                    expect(page.locator('.news')).to_have_count(1)
                    page.get_by_role('button', name='すべて解除', exact=True).click()
                    expect(page.locator('.news')).to_have_count(3)
                    page.get_by_role('button', name='話題タグ「ロボット」で絞り込む', exact=True).click()
                    expect(page.locator('.news')).to_have_count(1)
                    expect(page.locator('#active-filters')).to_contain_text('ロボット')
                    page.screenshot(path=str(output / f'{label}-list.png'), full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    row = page.locator('.news').first
                    row.locator('.more-actions summary').click()
                    row.get_by_role('button', name='記事の詳細', exact=True).click()
                    expect(page.locator('#detail')).to_be_visible()
                    page.locator('#detail-body').get_by_role('button', name='話題タグ「ロボット」で絞り込む', exact=True).click()
                    expect(page.locator('#detail')).not_to_be_visible()
                    expect(page.locator('.news')).to_have_count(1)
                    if mobile:
                        page.locator('#classification-open').click()
                        page.set_viewport_size({'width': 1280, 'height': 900})
                        expect(page.locator('#classification-dialog')).not_to_be_visible()
                        expect(page.locator('#classification #classification-controls')).to_have_count(1)
                    assert not errors, errors
                    results.append({'viewport': label, 'category_tags_source_intersection': True, 'tag_or': True, 'clear_and_article_tag': True, 'sources': 750, 'multiple_kinds_and_api_paging': True, 'all_custom_settings_toggle': True, 'console_errors': errors})
                    page.close()
                browser.close()
            (output / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
            print(json.dumps({'result': 'passed', 'checks': results}, ensure_ascii=False))
        finally:
            server.shutdown(); server.server_close(); thread.join(); server.collector.close(); server.enricher.close(); store.close()


if __name__ == '__main__':
    main()
