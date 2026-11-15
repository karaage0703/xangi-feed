"""Custom classification must persist without an explicit update click."""
import json
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
        podcast_source=store.source(store.add_source('Podcast配信元','https://example.org/feed','podcast'))
        store.ingest(podcast_source,[{'url':'https://example.org/podcast','title':'Podcast fixture','summary':'fixture','topics':['ロボット'],'published':None}])
        podcast_item=next(item for item in store.list_items('all')['items'] if item['kind']=='podcast')
        store.change_tag(podcast_item['id'],'add','音声')
        saved = store.save_custom_filter({'name': '分類テスト', 'recommendations': dict(RECOMMENDATION_DEFAULTS)})
        server = Server(store, 'fixture-token')
        thread = threading.Thread(target=server.serve_forever); thread.start()
        checks=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True, executable_path='/usr/bin/chromium-browser')
                for width in [1280,393,360]:
                    store.save_custom_filter(saved)
                    with store.transaction() as db: db.execute("DELETE FROM metadata WHERE key='custom-default-classification'")
                    context=browser.new_context(viewport={'width':width,'height':1000},extra_http_headers={'Authorization':'Bearer fixture-token'})
                    page=context.new_page(); page.goto(f'http://127.0.0.1:{server.server_port}')
                    tab=page.locator(f'[data-custom-filter="{saved["id"]}"]')
                    tab.click(); expect(page.locator('.news')).to_have_count(2)
                    if width < 800: page.locator('#classification-open').click()
                    page.locator('#kind-choices [data-kind=podcast]').click()
                    page.locator('#topic-choices [data-topic=ロボット]').click()
                    page.locator('#manual-tag-choices [data-tag=音声]').click()
                    page.locator('#source').select_option(str(podcast_source['id']))
                    if width < 800: page.locator('#classification-close').click()
                    page.locator('#search').fill('Podcast')
                    page.locator('#sort').select_option('preference')
                    expect(page.locator('.news')).to_have_count(1)
                    page.locator('[data-tab=all]').click()
                    tab.click()
                    expect(page.locator('.news')).to_have_count(1)
                    persisted=store.custom_filter(saved['id'])
                    for key,value in {'kinds':['podcast'],'topics':['ロボット'],'tags':['音声'],'source':str(podcast_source['id']),'search':'Podcast','sort':'preference'}.items(): assert persisted[key]==value, (key,persisted[key])
                    page.reload(); tab.click(); expect(page.locator('.news')).to_have_count(1)
                    page.locator('[data-tab=recommended]').click()
                    expect(page.locator('.news')).to_have_count(2)
                    if width < 800: page.locator('#classification-open').click()
                    page.locator('#kind-choices [data-kind=rss]').click()
                    if width < 800: page.locator('#classification-close').click()
                    expect(page.locator('.news')).to_have_count(1)
                    page.locator('[data-tab=all]').click()
                    page.locator('[data-tab=recommended]').click()
                    expect(page.locator('.news')).to_have_count(1)
                    expect(page.locator('.news')).to_contain_text('Article fixture')
                    page.reload(); expect(page.locator('.news')).to_contain_text('Article fixture')
                    other=browser.new_context(viewport={'width':width,'height':1000},extra_http_headers={'Authorization':'Bearer fixture-token'})
                    second=other.new_page(); second.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(second.locator('.news')).to_have_count(1)
                    expect(second.locator('.news')).to_contain_text('Article fixture')
                    second.locator(f'[data-custom-filter="{saved["id"]}"]').click()
                    expect(second.locator('.news')).to_have_count(1)
                    expect(second.locator('.news')).to_contain_text('Podcast fixture')
                    second.screenshot(path=str(output/f'autosave-{width}.png'),full_page=True)
                    # A failed autosave must block tab changes until retry succeeds.
                    page.locator(f'[data-custom-filter="{saved["id"]}"]').click()
                    page.route('**/api/custom-filters/classification',lambda route:route.fulfill(status=503,content_type='application/json',body='{"error":"fixture save failure"}'))
                    if width < 800: page.locator('#classification-open').click()
                    page.locator('#kind-choices [data-kind=rss]').click()
                    if width < 800: page.locator('#classification-close').click()
                    expect(page.locator('#classification-save-message')).to_contain_text('保存できませんでした')
                    page.locator('[data-tab=all]').click()
                    expect(page.locator(f'[data-custom-filter="{saved["id"]}"]')).to_have_attribute('aria-pressed','true')
                    page.unroute('**/api/custom-filters/classification')
                    page.locator('#classification-save-retry').click()
                    expect(page.locator('#classification-save-retry')).not_to_be_visible()
                    page.locator('[data-tab=all]').click()
                    expect(page.locator('[data-tab=all]')).to_have_attribute('aria-pressed','true')
                    checks.append({'width':width,'autosave_tab_switch_reload_other_context':'passed'})
                    other.close(); context.close()
                browser.close()
            (output/'browser-results.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
        finally:
            server.shutdown();server.server_close();thread.join();store.close()
if __name__=='__main__':main()
