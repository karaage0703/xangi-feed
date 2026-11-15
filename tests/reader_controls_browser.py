"""Read status, temporary search, sidebar editing and compact source picker."""
import json
import os
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
    engine=os.environ.get('FEED_BROWSER_ENGINE','chromium')
    with sync_playwright() as p:
        browser=p.webkit.launch(headless=True,executable_path=os.environ.get('PLAYWRIGHT_WEBKIT_EXECUTABLE')) if engine=='webkit' else p.chromium.launch(headless=True,executable_path='/usr/bin/chromium-browser')
        for width in [1280,393,360]:
            with tempfile.TemporaryDirectory() as directory:
                store=Store(Path(directory)/'db',seed=False);store.save_settings({'enabled':False})
                sources=[store.source(store.add_source(f'配信元{index:03d}',f'https://feed{index}.example/feed','podcast' if index==758 else 'rss')) for index in range(759)]
                items={}
                for title in ['Robot alpha','Robot beta','Other read','Skipped']:
                    items[title]=store.add_article({'url':'https://example.org/'+title.replace(' ','-'),'title':title})[0]
                for title,source in [('Robot alpha',sources[-1]),('Robot beta',sources[0])]:
                    store.ingest(source,[{'url':items[title]['url'],'title':title,'summary':'fixture','topics':['ロボット'],'published':None}])
                with store.transaction() as db:db.execute('UPDATE items SET kind=? WHERE id=?',('video',items['Robot beta']['id']))
                store.react(items['Other read']['id'],{'read':True});store.react(items['Skipped']['id'],{'muted':True})
                saved=store.save_custom_filter({'name':'保存したRobot','search':'Robot','recommendations':dict(RECOMMENDATION_DEFAULTS),'conditions':{**VIEW_DEFAULTS,'read':'any','muted':'include'}})
                server=Server(store,'fixture');worker=threading.Thread(target=server.serve_forever);worker.start()
                context=browser.new_context(viewport={'width':width,'height':1000},extra_http_headers={'Authorization':'Bearer fixture'})
                page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                origin=f'http://127.0.0.1:{server.server_port}'
                def choose(view_id):
                    if page.locator('#view-settings').is_visible():page.locator('#view-settings-close').click()
                    if width<901:page.locator('#classification-open').click()
                    page.locator(f'[data-custom-filter="{view_id}"]').click()
                    if width<901:expect(page.locator('#classification-dialog')).not_to_be_visible()
                def settings(view_id):
                    if width<901:page.locator('#classification-open').click()
                    page.locator(f'.view-row:has([data-custom-filter="{view_id}"]) .view-settings-button').click()
                    expect(page.locator('#view-settings')).to_be_visible()
                def close_settings():
                    page.locator('#view-settings-close').click();expect(page.locator('#view-settings')).not_to_be_visible()
                try:
                    page.goto(origin);expect(page.locator('#current-view-title')).to_have_text('カスタム')
                    expect(page.locator('#search')).to_be_visible()
                    assert page.locator('.news').first.bounding_box()['y']<500
                    choose('preset-all');expect(page.locator('.news')).to_have_count(4)
                    read_row=page.locator(f'.news[data-id="{items["Other read"]["id"]}"]')
                    expect(read_row.locator('.reading-state')).to_have_text('読了')
                    alpha=page.locator(f'.news[data-id="{items["Robot alpha"]["id"]}"]')
                    expect(alpha.locator('.reading-state')).to_have_text('未読')
                    expect(alpha.locator('.read-control svg')).to_have_count(1)
                    expect(alpha.locator('.read-control')).to_have_text('')
                    expect(alpha.locator('.read-control')).to_have_attribute('aria-pressed','false')
                    assert alpha.locator('.read-control').bounding_box()['height']==alpha.locator('.bookmark-control').bounding_box()['height']
                    assert alpha.locator('.read-control').bounding_box()['width']==alpha.locator('.bookmark-control').bounding_box()['width']
                    assert page.locator('#quick-read button').first.bounding_box()['height']<=36
                    for control in alpha.locator('.actions > button, .actions > .action-menu').all():
                        box=control.bounding_box();card=alpha.bounding_box()
                        if box:assert box['x']+box['width']<=card['x']+card['width']+1
                    alpha.locator('.read-control').click();expect(alpha.locator('.reading-state')).to_have_text('読了')
                    expect(alpha.locator('.read-control')).to_have_attribute('aria-pressed','true')
                    expect(page.locator('.news')).to_have_count(4)
                    alpha.locator('.read-control').click();expect(alpha.locator('.reading-state')).to_have_text('未読')
                    beta=page.locator(f'.news[data-id="{items["Robot beta"]["id"]}"]')
                    expect(beta.locator('.reading-state')).to_have_text('未視聴')
                    beta.locator('.read-control').click();expect(beta.locator('.reading-state')).to_have_text('視聴済み')
                    beta.locator('.read-control').click();expect(beta.locator('.reading-state')).to_have_text('未視聴')
                    choose(saved['id']);expect(page.locator('.news')).to_have_count(2)
                    before=store.custom_filter(saved['id'])
                    page.locator('#search').fill('alpha');expect(page.locator('.news')).to_have_count(1)
                    assert store.custom_filter(saved['id'])==before
                    page.locator('#search-clear').click();expect(page.locator('.news')).to_have_count(2)
                    page.reload();choose(saved['id']);expect(page.locator('#search')).to_have_value('')
                    expect(page.locator('.news')).to_have_count(2)
                    settings(saved['id']);expect(page.locator('#view-meta-group')).to_have_attribute('open','')
                    page.locator('#view-classification-group > summary').click()
                    page.locator('#saved-search').fill('alpha');expect(page.locator('.news')).to_have_count(1)
                    expect(page.locator('#source')).not_to_be_visible()
                    assert page.locator('#source-picker-open').bounding_box()['height']<=60
                    page.locator('#source-picker-open').click()
                    expect(page.locator('#source-search')).to_be_focused()
                    expect(page.locator('#source-results button')).to_have_count(51)
                    page.locator('#source-search').fill('758')
                    expect(page.locator('#source-results button')).to_have_count(2)
                    page.locator('#source-results').get_by_role('button',name='配信元758',exact=True).click()
                    expect(page.locator('#source-picker')).not_to_be_visible()
                    expect(page.locator('#source-picker-open')).to_have_text('配信元758')
                    close_settings();assert store.custom_filter(saved['id'])['source']==str(sources[-1]['id'])
                    page.locator('#search').fill('beta');expect(page.locator('.news')).to_have_count(0)
                    assert store.custom_filter(saved['id'])['search']=='alpha'
                    page.locator('#search-clear').click();expect(page.locator('.news')).to_have_count(1)
                    settings(saved['id']);page.locator('#custom-filter-name').fill('名前変更')
                    page.locator('#custom-filter-update').click();expect(page.locator('#current-view-title')).to_have_text('名前変更')
                    page.locator('#custom-filter-duplicate').click();page.locator('#custom-filter-new-name').fill('複製一覧')
                    page.locator('#custom-filter-new-name').press('Enter')
                    expect(page.locator('#view-settings')).not_to_be_visible()
                    expect(page.locator('#current-view-title')).to_have_text('複製一覧')
                    clone=next(v for v in store.custom_filters() if v['name']=='複製一覧')
                    assert clone['search']=='alpha' and clone['source']==str(sources[-1]['id'])
                    assert store.custom_filter(saved['id'])['name']=='名前変更'
                    settings(clone['id'])
                    page.locator('#recommendation-toggle > summary').click()
                    page.locator('[name=max_preference]').fill('100');expect(page.locator('.news')).to_have_count(0)
                    page.locator('[name=max_preference]').fill('');expect(page.locator('.news')).to_have_count(1)
                    page.locator('[name=recommendation_min_preference]').fill('100');expect(page.locator('.news')).to_have_count(0)
                    page.locator('[name=recommendation_min_preference]').fill('');expect(page.locator('.news')).to_have_count(1)
                    page.locator('#view-state-group > summary').click()
                    page.route('**/api/views/save',lambda route:route.fulfill(status=503,content_type='application/json',body='{"error":"fixture failure"}'))
                    page.locator('#view-state-form [name=noted]').select_option('yes')
                    expect(page.locator('#settings-save-message')).to_contain_text('保存できませんでした')
                    page.locator('#view-settings-close').click();expect(page.locator('#view-settings')).to_be_visible()
                    page.unroute('**/api/views/save');page.locator('#settings-save-retry').click()
                    expect(page.locator('#settings-save-retry')).not_to_be_visible()
                    expect(page.locator('.news')).to_have_count(0)
                    page.locator('#view-state-form [name=noted]').select_option('any');expect(page.locator('.news')).to_have_count(1)
                    close_settings()
                    page.locator('#quick-read [data-read-filter=read]').click();expect(page.locator('.news')).to_have_count(0)
                    page.locator('#quick-read [data-read-filter=any]').click();expect(page.locator('.news')).to_have_count(1)
                    page.locator('#quick-read [data-read-filter=unread]').click();expect(page.locator('.news')).to_have_count(1)
                    page.locator('.news .read-control').click();expect(page.locator('.news')).to_have_count(0)
                    if width<901:expect(page.locator('#classification-open')).to_be_focused()
                    page.locator('#quick-read [data-read-filter=any]').click();expect(page.locator('.news')).to_have_count(1)
                    other=browser.new_context(viewport={'width':width,'height':1000},extra_http_headers={'Authorization':'Bearer fixture'})
                    second=other.new_page();second.goto(origin)
                    if width<901:second.locator('#classification-open').click()
                    second.locator(f'[data-custom-filter="{clone["id"]}"]').click()
                    if width<901:expect(second.locator('#classification-dialog')).not_to_be_visible()
                    expect(second.locator('.news')).to_have_count(1)
                    expect(second.locator('#search')).to_have_value('')
                    assert second.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    second.screenshot(path=str(out/f'reader-{width}.png'),full_page=True)
                    if width<901:second.locator('#classification-open').click()
                    second.locator(f'.view-row:has([data-custom-filter="{clone["id"]}"]) .view-settings-button').click()
                    second.screenshot(path=str(out/f'settings-{width}.png'))
                    other.close();assert not errors,errors
                    checks.append({'engine':engine,'width':width,'read_status_search_isolation_sidebar_source_picker_retry_cross_context':'passed'})
                finally:
                    context.close();server.shutdown();server.server_close();worker.join();store.close()
        browser.close()
    (out/'browser-results.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
