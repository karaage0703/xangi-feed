"""Separate list management from filters, and make unrestricted tags explicit."""
import json
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect,sync_playwright
from xangi_feed.store import Store,RECOMMENDATION_DEFAULTS,VIEW_DEFAULTS
from xangi_feed.server import Server


def main():
    out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium-browser')
        for width in [1280,393,360]:
            with tempfile.TemporaryDirectory() as directory:
                store=Store(Path(directory)/'db',seed=False);store.save_settings({'enabled':False})
                source=store.source(store.add_source('Podcast fixture','https://example.org/feed','podcast'))
                store.ingest(source,[{'url':'https://example.org/alpha','title':'Robot alpha','summary':'fixture','topics':['ロボット'],'published':None},{'url':'https://example.org/beta','title':'Science beta','summary':'fixture','topics':['科学'],'published':None}])
                items={item['title']:item for item in store.list_items('all')['items']}
                store.change_tag(items['Robot alpha']['id'],'add','既存タグ')
                other,_=store.add_article({'url':'https://example.org/other','title':'Other read'});store.react(other['id'],{'read':True})
                saved=store.save_custom_filter({'name':'Podcast','kinds':['podcast'],'recommendations':dict(RECOMMENDATION_DEFAULTS),'conditions':{**VIEW_DEFAULTS,'read':'any','muted':'include'}})
                server=Server(store,'fixture');worker=threading.Thread(target=server.serve_forever);worker.start()
                context=browser.new_context(viewport={'width':width,'height':1000},extra_http_headers={'Authorization':'Bearer fixture'})
                page=context.new_page();errors=[];filter_saves=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda request:filter_saves.append(request.url) if request.url.endswith('/api/views/save') else None)
                try:
                    origin=f'http://127.0.0.1:{server.server_port}';page.goto(origin)
                    expect(page.locator('#quick-read')).to_have_count(0)
                    expect(page.locator('[name=read]')).to_have_count(1)
                    if width<901:page.locator('#classification-open').click()
                    page.locator(f'.view-row:has([data-custom-filter="{saved["id"]}"]) .view-settings-button').click()
                    expect(page.locator('#view-settings')).to_be_visible()
                    expect(page.locator('#filter-dialog')).not_to_be_visible()
                    expect(page.locator('#view-settings [name=read], #view-settings #kind-choices')).to_have_count(0)
                    before_rename=store.custom_filter(saved['id'])
                    page.locator('#custom-filter-name').fill('Podcast renamed');page.locator('#custom-filter-update').click()
                    expect(page.locator('#current-view-title')).to_have_text('Podcast renamed')
                    page.locator('#view-settings-close').click();expect(page.locator('#view-settings')).not_to_be_visible()
                    assert store.custom_filter(saved['id'])=={**before_rename,'name':'Podcast renamed'}
                    assert not filter_saves,filter_saves
                    expect(page.locator('.news')).to_have_count(2)
                    page.locator('#view-filter-open').click()
                    expect(page.locator('#filter-dialog')).to_be_visible()
                    expect(page.locator('#view-settings')).not_to_be_visible()
                    expect(page.locator('#kind-choices')).to_be_visible()
                    expect(page.locator('#filter-dialog #custom-filter-name')).to_have_count(0)
                    assert page.locator('#kind-choices').bounding_box()['y']<page.locator('#view-state-group > summary').bounding_box()['y']
                    page.locator('#view-classification-group > summary').click()
                    expect(page.locator('#topic-no-filter')).to_have_attribute('aria-pressed','true')
                    expect(page.locator('#tag-no-filter')).to_have_attribute('aria-pressed','true')
                    topic_count=page.locator('#topic-choices button').count()
                    page.locator('#topic-choices [data-topic=ロボット]').click();expect(page.locator('.news')).to_have_count(1)
                    page.locator('#topic-no-filter').click();expect(page.locator('.news')).to_have_count(2)
                    page.locator('#manual-tag-choices [data-tag=既存タグ]').click();expect(page.locator('.news')).to_have_count(1)
                    page.locator('#tag-no-filter').click();expect(page.locator('.news')).to_have_count(2)
                    assert store.item(items['Robot alpha']['id'])['tags']==['既存タグ']
                    page.locator('#kind-choices [data-kind=""]').click();expect(page.locator('.news')).to_have_count(3)
                    page.locator('#kind-choices [data-kind=podcast]').click();expect(page.locator('.news')).to_have_count(2)
                    page.locator('#source-picker-open').click();page.locator('#source-results').get_by_role('button',name='Podcast fixture',exact=True).click()
                    page.locator('#filter-dialog-close').click();expect(page.locator('#filter-dialog')).not_to_be_visible()
                    beta=page.locator(f'.news[data-id="{items["Science beta"]["id"]}"]')
                    beta.locator('.more-actions > summary').click();beta.get_by_role('button',name='タグを編集',exact=True).click()
                    page.locator('#manual-tag-input').fill('自由な新規タグ')
                    page.locator('#detail').get_by_role('button',name='タグを追加',exact=True).click()
                    expect(page.locator('#detail .manual-tag-assigned')).to_contain_text('自由な新規タグ')
                    page.locator('#detail-close').click()
                    page.locator('#view-filter-open').click();page.locator('#view-classification-group > summary').click()
                    expect(page.locator('#manual-tag-choices [data-tag=自由な新規タグ]')).to_have_count(1)
                    assert page.locator('#topic-choices button').count()==topic_count
                    page.locator('#manual-tag-choices [data-tag=自由な新規タグ]').click();expect(page.locator('.news')).to_have_count(1)
                    page.locator('#tag-no-filter').click();expect(page.locator('.news')).to_have_count(2)
                    page.locator('#view-state-group > summary').click()
                    page.locator('[name=read]').select_option('read');expect(page.locator('.news')).to_have_count(0)
                    page.locator('[name=read]').select_option('any');expect(page.locator('.news')).to_have_count(2)
                    page.locator('[name=muted]').select_option('only');expect(page.locator('.news')).to_have_count(0)
                    page.locator('#filter-dialog-close').click()
                    expect(page.locator('#active-view-summary')).to_have_count(0)
                    page.get_by_role('button',name='条件を編集',exact=True).click();expect(page.locator('#filter-dialog')).to_be_visible()
                    page.locator('[name=muted]').select_option('include');expect(page.locator('.news')).to_have_count(2)
                    page.locator('#filter-dialog-close').click()
                    snapshot=store.custom_filter(saved['id']);page.locator('#search').fill('alpha');expect(page.locator('.news')).to_have_count(1)
                    assert store.custom_filter(saved['id'])==snapshot
                    page.locator('#search-clear').click();expect(page.locator('.news')).to_have_count(2)
                    page.locator('#view-filter-open').click();page.locator('#view-classification-group > summary').click()
                    page.route('**/api/views/save',lambda route:route.fulfill(status=503,content_type='application/json',body='{"error":"fixture failure"}'))
                    page.locator('#topic-choices [data-topic=科学]').click()
                    expect(page.locator('#filter-save-message')).to_contain_text('保存できませんでした')
                    page.locator('#filter-dialog-close').click();expect(page.locator('#filter-dialog')).to_be_visible()
                    page.unroute('**/api/views/save');page.locator('#filter-save-retry').click()
                    expect(page.locator('#filter-save-retry')).not_to_be_visible();expect(page.locator('.news')).to_have_count(1)
                    page.locator('#topic-no-filter').click();expect(page.locator('.news')).to_have_count(2)
                    page.screenshot(path=str(out/f'filters-{width}.png'))
                    page.locator('#filter-dialog-close').click()
                    assert store.custom_filter(saved['id'])['topics']==[] and store.custom_filter(saved['id'])['tags']==[]
                    other_context=browser.new_context(viewport={'width':width,'height':1000},extra_http_headers={'Authorization':'Bearer fixture'})
                    second=other_context.new_page();second.goto(origin)
                    if width<901:second.locator('#classification-open').click()
                    second.locator(f'[data-custom-filter="{saved["id"]}"]').click()
                    if width<901:expect(second.locator('#classification-dialog')).not_to_be_visible()
                    expect(second.locator('.news')).to_have_count(2)
                    second.locator('#view-filter-open').click();second.locator('#view-classification-group > summary').click()
                    expect(second.locator('#topic-no-filter')).to_have_attribute('aria-pressed','true');expect(second.locator('#tag-no-filter')).to_have_attribute('aria-pressed','true')
                    second.locator('#filter-dialog-close').click()
                    second.screenshot(path=str(out/f'reader-{width}.png'),full_page=True)
                    if width<901:second.locator('#classification-open').click()
                    second.locator(f'.view-row:has([data-custom-filter="{saved["id"]}"]) .view-settings-button').click()
                    second.screenshot(path=str(out/f'list-settings-{width}.png'))
                    assert second.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    other_context.close();assert not errors,errors
                    checks.append({'width':width,'separate_settings_filters_type_first_unrestricted_tags_dynamic_tags_retry_restore':'passed'})
                finally:
                    context.close();server.shutdown();server.server_close();worker.join();store.close()
        browser.close()
    (out/'browser-results.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
