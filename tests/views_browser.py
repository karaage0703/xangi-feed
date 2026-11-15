"""Sidebar lists, common filters and cross-computer persistence, fixture only."""
import json
import tempfile
import threading
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect,sync_playwright
from xangi_feed.store import Store,RECOMMENDATION_DEFAULTS
from xangi_feed.server import Server


def main():
    out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
    checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium-browser',headless=True)
        for width in [1280,393,360]:
            with tempfile.TemporaryDirectory() as directory:
                store=Store(Path(directory)/'db',seed=False);store.save_settings({'enabled':False})
                items={}
                for name in ['Unread fixture','Read fixture','Skip fixture','Saved fixture']:
                    item,_=store.add_article({'url':'https://example.org/'+name.split()[0],'title':name})
                    items[name]=item
                store.react(items['Read fixture']['id'],{'read':True})
                store.react(items['Skip fixture']['id'],{'muted':True})
                store.react(items['Saved fixture']['id'],{'saved':True})
                source=store.source(store.add_source('Podcast配信元','https://example.org/feed','podcast'))
                store.ingest(source,[{'url':items['Unread fixture']['url'],'title':'Unread fixture','summary':'fixture','topics':['ロボット'],'published':None}])
                store.change_tag(items['Unread fixture']['id'],'add','音声')
                legacy=store.save_custom_filter({'name':'以前の一覧','recommendations':dict(RECOMMENDATION_DEFAULTS)})
                server=Server(store,'fixture');worker=threading.Thread(target=server.serve_forever);worker.start()
                context=browser.new_context(viewport={'width':width,'height':1000},extra_http_headers={'Authorization':'Bearer fixture'})
                page=context.new_page();errors=[];saves=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda request:saves.append(request.url) if request.url.endswith('/api/views/save') else None)
                def choose(view):
                    if width<901:page.locator('#classification-open').click()
                    page.locator(f'[data-custom-filter="{view}"]').focus()
                    page.locator(f'[data-custom-filter="{view}"]').press('Enter')
                    expect(page.locator(f'[data-custom-filter="{view}"]')).to_have_attribute('aria-pressed','true')
                    if width<901:expect(page.locator('#classification-dialog')).not_to_be_visible()
                try:
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(page.locator('#current-view-title')).to_have_text('カスタム')
                    expect(page.locator('.news')).to_have_count(2)
                    assert not saves,saves
                    choose(legacy['id'])
                    expect(page.locator('.news')).to_have_count(2)
                    assert not saves,saves
                    page.locator('#filter-toggle > summary').click()
                    page.locator('#kind-choices [data-kind=podcast]').click()
                    page.locator('#topic-choices [data-topic=ロボット]').click()
                    page.locator('#manual-tag-choices [data-tag=音声]').click()
                    page.locator('#source').select_option(str(source['id']))
                    expect(page.locator('.news')).to_have_count(1)
                    expect(page.locator('.news')).to_contain_text('Unread fixture')
                    choose('preset-read');expect(page.locator('.news')).to_have_count(1)
                    expect(page.locator('.news')).to_contain_text('Read fixture')
                    if not page.locator('#view-state-form').is_visible():page.locator('#filter-toggle > summary').click()
                    expect(page.locator('#view-state-form [name=read]')).to_have_value('read')
                    page.locator('#view-state-form [name=read]').select_option('any')
                    expect(page.locator('.news')).to_have_count(3)
                    page.locator('#view-state-form [name=muted]').select_option('include')
                    expect(page.locator('.news')).to_have_count(4)
                    choose('preset-muted');expect(page.locator('.news')).to_have_count(1)
                    expect(page.locator('.news')).to_contain_text('Skip fixture')
                    page.locator('#view-state-form [name=saved]').select_option('yes')
                    expect(page.locator('.news')).to_have_count(0)
                    page.locator('#view-state-form [name=saved]').select_option('any')
                    expect(page.locator('.news')).to_have_count(1)
                    choose('preset-read');expect(page.locator('.news')).to_have_count(4)
                    if width<901:page.locator('#classification-open').click()
                    page.locator('#custom-filter-plus').click()
                    page.locator('#custom-filter-new-name').fill('全部カスタム')
                    page.locator('#custom-filter-new-name').press('Enter')
                    expect(page.locator('#custom-filter-new')).not_to_be_visible()
                    if width<901:expect(page.locator('#classification-dialog')).not_to_be_visible()
                    expect(page.locator('#current-view-title')).to_have_text('全部カスタム')
                    selected=page.locator('#custom-filter-tabs button[aria-pressed=true]').get_attribute('data-custom-filter')
                    page.locator('#view-state-form [name=read]').select_option('unread')
                    page.locator('#view-state-form [name=muted]').select_option('exclude')
                    page.locator('#view-state-form [name=saved]').select_option('yes')
                    expect(page.locator('.news')).to_have_count(1)
                    expect(page.locator('.news')).to_contain_text('Saved fixture')
                    page.locator('#view-state-form [name=max_preference]').fill('100')
                    expect(page.locator('.news')).to_have_count(0)
                    page.locator('#view-state-form [name=max_preference]').fill('')
                    expect(page.locator('.news')).to_have_count(1)
                    # Editing the lower preference bound is also autosaved now.
                    page.locator('[name=recommendation_min_preference]').fill('100')
                    expect(page.locator('.news')).to_have_count(0)
                    page.locator('[name=recommendation_min_preference]').fill('')
                    expect(page.locator('.news')).to_have_count(1)
                    other=browser.new_context(viewport={'width':width,'height':1000},extra_http_headers={'Authorization':'Bearer fixture'})
                    second=other.new_page();second.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(second.locator('#current-view-title')).to_have_text('カスタム')
                    if width<901:second.locator('#classification-open').click()
                    second.locator(f'[data-custom-filter="{selected}"]').click()
                    if width<901:expect(second.locator('#classification-dialog')).not_to_be_visible()
                    expect(second.locator('.news')).to_have_count(1)
                    expect(second.locator('.news')).to_contain_text('Saved fixture')
                    second.locator('#filter-toggle > summary').click()
                    expect(second.locator('#view-state-form [name=saved]')).to_have_value('yes')
                    assert second.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    second.screenshot(path=str(out/f'filters-{width}.png'),full_page=True)
                    second.locator('#filter-toggle > summary').click()
                    second.screenshot(path=str(out/f'views-{width}.png'),full_page=True)
                    if width<901:second.locator('#classification-open').click()
                    second.locator(f'[data-custom-filter="{legacy["id"]}"]').click()
                    expect(second.locator('.news')).to_have_count(1)
                    expect(second.locator('.news')).to_contain_text('Unread fixture')
                    second.locator('#filter-toggle > summary').click()
                    expect(second.locator('#kind-choices [data-kind=podcast]')).to_have_attribute('aria-pressed','true')
                    expect(second.locator('#source')).to_have_value(str(source['id']))
                    other.close()
                    # Failed filter saves keep input and stop switching to another list.
                    page.route('**/api/views/save',lambda route:route.fulfill(status=503,content_type='application/json',body='{"error":"fixture failure"}'))
                    page.locator('#view-state-form [name=noted]').select_option('yes')
                    expect(page.locator('#classification-save-message')).to_contain_text('保存できませんでした')
                    if width<901:page.locator('#classification-open').click()
                    page.locator('[data-custom-filter="preset-all"]').click()
                    expect(page.locator('#current-view-title')).to_have_text('全部カスタム')
                    if width<901:expect(page.locator('#classification-dialog')).not_to_be_visible()
                    page.unroute('**/api/views/save')
                    page.locator('#classification-save-retry').click()
                    expect(page.locator('#classification-save-retry')).not_to_be_visible()
                    expect(page.locator('.news')).to_have_count(0)
                    page.locator('#view-state-form [name=noted]').select_option('any')
                    expect(page.locator('.news')).to_have_count(1)
                    # Removing a bookmark must refilter a saved-only view immediately.
                    page.locator('.news .bookmark-control').click()
                    expect(page.locator('.news')).to_have_count(0)
                    choose('preset-all');expect(page.locator('.news')).to_have_count(4)
                    # Initial presets participate in reordering too.
                    page.locator('#custom-filter-editor summary').click()
                    page.locator('#custom-filter-left').click()
                    expect(page.locator('#custom-filter-tabs button').nth(2)).to_have_attribute('data-custom-filter','preset-all')
                    if width==1280:
                        page.locator('#custom-filter-tabs button').first.drag_to(page.locator('#custom-filter-tabs button').nth(1))
                        expect(page.locator('#custom-filter-tabs button').first).to_have_attribute('data-custom-filter',legacy['id'])
                    # Long menus remain scrollable and the create action remains reachable.
                    if width==360:
                        for index in range(98):store.save_custom_filter({'name':f'追加一覧{index}','recommendations':dict(RECOMMENDATION_DEFAULTS)})
                        page.reload()
                        if width<901:page.locator('#classification-open').click()
                        expect(page.locator('#custom-filter-tabs button')).to_have_count(109)
                        page.locator('#custom-filter-tabs button').last.click()
                        expect(page.locator('#classification-dialog')).not_to_be_visible()
                        expect(page.locator('#current-view-title')).to_have_text('追加一覧97')
                        page.locator('#classification-open').click();page.locator('#custom-filter-plus').click()
                        page.locator('#custom-filter-new-name').fill('上限確認');page.locator('#custom-filter-new-name').press('Enter')
                        expect(page.locator('#custom-filter-new-message')).to_contain_text('100')
                        page.locator('#custom-filter-new-cancel').click()

                    # Deleting every list is allowed; reload must not recreate presets.
                    for record in store.custom_filters():store.delete_custom_filter(record['id'])
                    page.reload();expect(page.locator('#custom-filter-tabs button')).to_have_count(0)
                    expect(page.get_by_text('保存した一覧はありません',exact=True)).to_be_visible()
                    page.get_by_role('button',name='一覧を追加',exact=True).click()
                    page.locator('#custom-filter-new-name').fill('再作成');page.locator('#custom-filter-new-name').press('Enter')
                    expect(page.locator('#current-view-title')).to_have_text('再作成')
                    self_count=len(store.ensure_custom_views());assert self_count==1,self_count
                    assert not errors,errors
                    checks.append({'width':width,'sidebar_edit_presets_status_score_create_cross_context':'passed'})
                finally:
                    context.close();server.shutdown();server.server_close();worker.join();store.close()
        browser.close()
    (out/'browser-results.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
