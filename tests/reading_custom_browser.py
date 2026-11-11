"""Exercise read-history order and immediate Custom conditions with fixture data."""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.environ.get('FEED_TEST_REPO', str(Path(__file__).resolve().parent.parent)))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / 'fixture.db', seed=False)
        ids = []
        for i in range(4):
            item, _ = store.add_article({'url': f'https://example.org/{i}', 'title': f'記事 {i}'})
            ids.append(item['id'])
            if i < 2:
                store.save_analysis(item['id'], store.content_hash(item), 'done', {'title_ja': item['title'], 'summary_ja': '確認用の要約', 'comment': '自分向けのコメント'})
        with store.transaction() as db:
            db.execute('UPDATE items SET updated=9999999999 WHERE id=?', (ids[0],))
        store.db.execute('UPDATE items SET topics=? WHERE id=?', (json.dumps(['ロボット']), ids[1])); store.db.commit()
        store.react(ids[1], {'rating': 1})
        server = Server(store, 'fixture-token-' * 4)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            checks = []
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
                for label, width in [('desktop', 1280), ('mobile', 393), ('small-mobile', 360)]:
                    for item in ids:
                        store.react(item, {'read': False, 'saved': False})
                    with store.transaction() as db:
                        db.execute('UPDATE items SET read=1 WHERE id=?', (ids[3],))
                    store.save_settings({'mark_read_on_open': True, 'recommendation_min_preference': None, 'recommendation_days': 0})
                    context = browser.new_context(viewport={'width': width, 'height': 900}, extra_http_headers={'Authorization': 'Bearer ' + 'fixture-token-' * 4})
                    context.route('https://example.org/**', lambda route: route.fulfill(content_type='text/html', body='<h1>Original article</h1>'))
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    panel = page.locator('#recommendation-panel')
                    form = page.locator('#recommendation-form')
                    expect(page.get_by_role('button', name='カスタム', exact=True)).to_have_attribute('aria-pressed', 'true')
                    expect(panel).to_be_visible()
                    expect(page.locator('#recommendation-fields')).to_be_enabled()
                    expect(form.locator('button[type=submit]')).to_have_count(0)
                    assert sorted(form.locator('input, select, textarea').evaluate_all('(nodes) => nodes.map(node => node.name)')) == sorted(['recommendation_min_preference', 'recommendation_priority', 'recommendation_days', 'recommendation_hide_bad', 'recommendation_mix_types'])
                    expect(page.locator('.news')).to_have_count(3)
                    page.screenshot(path=str(output / f'{label}-custom.png'), full_page=True)

                    page.locator('#recommendation-toggle > summary').click()

                    # Failed saves keep the previous results and offer an explicit retry.
                    page.route('**/api/settings', lambda route: route.fulfill(status=503, json={'error': 'Fixture offline'}) if route.request.method == 'POST' else route.continue_())
                    form.locator('[name=recommendation_min_preference]').fill('2'); form.locator('[name=recommendation_min_preference]').press('Tab')
                    expect(page.locator('#recommendation-retry')).to_be_visible()
                    expect(page.locator('#recommendation-message')).to_contain_text('まだ反映されていません')
                    expect(page.locator('.news')).to_have_count(3)
                    page.screenshot(path=str(output / f'{label}-save-error.png'), full_page=True)
                    page.unroute('**/api/settings')
                    page.locator('#recommendation-retry').click()
                    expect(page.locator('#recommendation-message')).to_have_text('保存して反映しました。')
                    expect(page.locator('.news')).to_have_count(1)
                    page.reload()
                    page.locator('#recommendation-toggle > summary').click()
                    expect(form.locator('[name=recommendation_min_preference]')).to_have_value('2')
                    expect(page.locator('.news')).to_have_count(1)

                    days = form.locator('[name=recommendation_days]')
                    days.fill('366')
                    days.press('Tab')
                    expect(page.locator('#recommendation-message')).to_contain_text('まだ保存していません')
                    assert store.settings()['recommendation_days'] == 0
                    days.fill('0')
                    days.press('Tab')
                    expect(page.locator('#recommendation-message')).to_have_text('保存して反映しました。')
                    if label == 'desktop':
                        minimum = form.locator('[name=recommendation_min_preference]')
                        minimum.focus()
                        page.keyboard.press('ArrowUp')
                        expect(page.locator('.news')).to_have_count(0)
                        page.keyboard.press('ArrowDown')
                        expect(page.locator('.news')).to_have_count(1)
                        expect(minimum).to_be_focused()

                    # Rapid changes are serialized. A save finishing on another tab must not switch it.
                    posts = []
                    def slow_save(route):
                        if route.request.method == 'POST':
                            posts.append(route.request.post_data_json)
                            time.sleep(.2)
                        route.continue_()
                    page.route('**/api/settings', slow_save)
                    page.evaluate("""() => {
                        const f=document.querySelector('#recommendation-form');
                        function set(v){f.elements.recommendation_min_preference.value=v;f.elements.recommendation_min_preference.dispatchEvent(new Event('change',{bubbles:true}));}
                        set('1');setTimeout(()=>set('3'),20);setTimeout(()=>set('0'),40);
                        setTimeout(()=>document.querySelector('[data-tab=all]').click(),60);
                    }""")
                    expect(page.locator('[data-tab=all]')).to_have_attribute('aria-pressed', 'true')
                    expect(page.locator('#recommendation-message')).to_have_text('保存して反映しました。')
                    assert posts[-1]['recommendation_min_preference'] == 0
                    assert store.settings()['recommendation_min_preference'] == 0
                    expect(panel).not_to_be_visible()
                    page.unroute('**/api/settings')
                    expect(page.locator('.news')).to_have_count(4)
                    page.locator('#sort').select_option('published')
                    row_a = page.locator(f'article[data-id="{ids[0]}"]')
                    row_a.get_by_role('button', name='ブックマーク', exact=True).click()
                    expect(row_a.get_by_role('button', name='ブックマーク済み', exact=True)).to_have_attribute('aria-pressed', 'true')
                    row_a.locator('.more-actions summary').click()
                    row_a.locator('.read-control').click()
                    expect(row_a.locator('.read-control')).to_have_text('未読に戻す')
                    row_b = page.locator(f'article[data-id="{ids[1]}"]')
                    with context.expect_page() as popup:
                        row_b.locator('.news-title').click()
                    popup.value.wait_for_load_state()
                    popup.value.close()
                    expect(row_b.locator('.read-control')).to_have_text('未読に戻す')

                    page.get_by_role('button', name='読了', exact=True).click()
                    expect(panel).not_to_be_visible()
                    expect(page.locator('#sort')).to_have_value('read_at')
                    expect(page.locator('.news-title')).to_have_text(['記事 1', '記事 0', '記事 3'])
                    expect(page.locator('.news').last).to_contain_text('読了日時不明')
                    page.screenshot(path=str(output / f'{label}-read.png'), full_page=True)
                    page.get_by_role('button', name='すべて', exact=True).click()
                    expect(page.locator('#sort')).to_have_value('published')
                    page.get_by_role('button', name='読了', exact=True).click()
                    expect(page.locator('#sort')).to_have_value('read_at')
                    page.reload()
                    page.locator('#recommendation-toggle > summary').click()
                    page.get_by_role('button', name='読了', exact=True).click()
                    expect(page.locator('#sort')).to_have_value('read_at')
                    expect(page.locator('.news-title')).to_have_text(['記事 1', '記事 0', '記事 3'])
                    page.locator('#sort').select_option('updated')
                    expect(page.locator('.news-title').first).to_have_text('記事 0')
                    page.reload()
                    page.locator('#recommendation-toggle > summary').click()
                    page.locator('[data-tab=read]').click()
                    expect(page.locator('#sort')).to_have_value('updated')
                    page.locator('#sort').select_option('read_at')
                    expect(page.locator('.news-title').first).to_have_text('記事 1')
                    row_a = page.locator(f'article[data-id="{ids[0]}"]')
                    row_a.locator('.more-actions summary').click()
                    row_a.locator('.read-control').click()
                    expect(page.locator('.news')).to_have_count(2)
                    page.locator('[data-tab=saved]').click()
                    expect(page.locator('.news-title')).to_have_text(['記事 0'])
                    expect(panel).not_to_be_visible()
                    page.locator('.news .more-actions summary').click()
                    page.get_by_role('button', name='記事の詳細', exact=True).click()
                    expect(page.locator('#detail')).to_be_visible()
                    page.locator('#detail-close').click()
                    expect(page.locator('#detail')).not_to_be_visible()
                    page.screenshot(path=str(output / f'{label}-bookmarks.png'), full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert not errors, errors
                    checks.append({'viewport': label, 'custom_only_conditions': True, 'autosave_retry_and_latest_wins': True, 'save_does_not_switch_tabs': True, 'read_time_default_and_order': True, 'bookmark_independent': True, 'console_errors': errors})
                    context.close()
                browser.close()
            (output / 'result.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2))
            print(json.dumps({'result': 'passed', 'checks': checks}, ensure_ascii=False))
        finally:
            server.shutdown(); server.server_close(); thread.join()
            server.collector.close(); server.enricher.close(); store.close()


if __name__ == '__main__':
    main()
