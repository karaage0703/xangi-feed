"""Mouse list ordering, delayed previews and isolated bookmark/read reactions."""
import base64,json,sys,tempfile,threading
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect,sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store,RECOMMENDATION_DEFAULTS,VIEW_DEFAULTS


def main():
    out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);results=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium-browser')
        for width in [1280,393,360]:
            with tempfile.TemporaryDirectory() as directory:
                store=Store(Path(directory)/'db',seed=False);store.save_settings({'enabled':False,'show_link_previews':True})
                source=store.source(store.add_source('Fixture podcast','https://example.org/feed','podcast'))
                store.ingest(source,[{'url':f'https://example.org/{n}','title':f'Podcast {n:03}','summary':'Fixture article excerpt','topics':['ロボット'],'published':None} for n in range(120)])
                def view(name,conditions):return store.save_custom_filter({'name':name,'kinds':['podcast'],'recommendations':dict(RECOMMENDATION_DEFAULTS),'conditions':{**VIEW_DEFAULTS,'read':'any','muted':'include',**conditions}})
                any_view=view('Any podcast',{});unread=view('Unread podcast',{'read':'unread'});saved=view('Saved podcast',{'saved':'yes'})
                server=Server(store,'fixture');worker=threading.Thread(target=server.serve_forever);worker.start()
                context=browser.new_context(viewport={'width':width,'height':1000},extra_http_headers={'Authorization':'Bearer fixture'})
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                lists=[];page.on('request',lambda request:lists.append(request.url) if urlsplit(request.url).path=='/api/items' else None)
                allow_preview=[False]
                page.route('**/api/items/*/preview',lambda route:route.fulfill(content_type='application/json',body=json.dumps({'status':'done' if allow_preview[0] else 'pending','has_image':allow_preview[0],'title':'Delayed preview title','site_name':'Fixture','description':'Delayed preview description'})))
                page.route('**/api/items/*/preview/image',lambda route:route.fulfill(content_type='image/png',body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4l8AAAAASUVORK5CYII=')))
                def menu():
                    if width<901 and not page.locator('#classification-dialog').evaluate('(e)=>e.open'):page.locator('#classification-open').click()
                def select(record):
                    menu();page.locator(f'[data-custom-filter="{record["id"]}"]').click();expect(page.locator('#current-view-title')).to_have_text(record['name'])
                    expect(page.locator('#feed')).to_have_attribute('aria-busy','false')
                def pointer_move(source_id,target_id,after=True):
                    handle=page.locator(f'[data-view-id="{source_id}"] .view-drag-handle');target=page.locator(f'[data-view-id="{target_id}"]')
                    h=handle.bounding_box();t=target.bounding_box();page.mouse.move(h['x']+h['width']/2,h['y']+h['height']/2);page.mouse.down();page.mouse.move(t['x']+t['width']/2,t['y']+t['height']-(2 if after else t['height']-2),steps=12)
                    expect(target).to_have_attribute('data-drop-position','after' if after else 'before');page.mouse.up()
                    expect(page.locator('#view-order-message')).to_contain_text('保存しました')
                    expect(page.locator('#custom-filter-plus')).to_be_enabled()
                try:
                    page.goto(f'http://127.0.0.1:{server.server_port}');expect(page.locator('#feed')).to_have_attribute('aria-busy','false');select(any_view)
                    menu();pointer_move(any_view['id'],saved['id'])
                    order=[item['id'] for item in store.custom_filters()];assert order.index(any_view['id'])==order.index(saved['id'])+1
                    pointer_move(any_view['id'],unread['id'],False)
                    order=[item['id'] for item in store.custom_filters()];assert order.index(any_view['id'])+1==order.index(unread['id'])
                    handle=page.locator(f'[data-view-id="{any_view["id"]}"] .view-drag-handle');handle.focus();handle.press('Alt+ArrowDown')
                    expect(page.locator('#custom-filter-plus')).to_be_enabled();expect(handle).to_be_focused()
                    assert [item['id'] for item in store.custom_filters()].index(any_view['id'])>[item['id'] for item in store.custom_filters()].index(unread['id'])
                    page.screenshot(path=str(out/f'sidebar-normal-{width}.png'))
                    before_fail=[item['id'] for item in store.custom_filters()]
                    page.route('**/api/custom-filters/reorder',lambda route:route.fulfill(status=503,content_type='application/json',body='{"error":"fixture reorder failure"}'))
                    pointer_move(any_view['id'],unread['id'],False)
                    expect(page.locator('#notice')).to_contain_text('並べ替えを保存できませんでした')
                    assert [item['id'] for item in store.custom_filters()]==before_fail
                    page.unroute('**/api/custom-filters/reorder')
                    # Restore the menu state; a second browser sees the same persisted order.
                    page.screenshot(path=str(out/f'sidebar-{width}.png'))
                    normal=page.locator(f'[data-custom-filter="{saved["id"]}"]');assert normal.evaluate('(e)=>getComputedStyle(e).borderWidth')=='0px'
                    if width<901:page.locator('#classification-close').click()
                    page.locator('#more').click();expect(page.locator('.news')).to_have_count(100)
                    page.locator('#more').click();expect(page.locator('.news')).to_have_count(120)
                    ids=page.locator('.news').evaluate_all('(rows)=>rows.map(row=>row.dataset.id)');target_id=ids[10];row=page.locator(f'.news[data-id="{target_id}"]')
                    row.scroll_into_view_if_needed();page.evaluate('(id)=>scrollTo(0,document.querySelector(`[data-id="${id}"]`).offsetTop+20)',target_id)
                    old_y=row.bounding_box()['y'];old_height=page.locator('.link-card').first.bounding_box()['height'];allow_preview[0]=True
                    expect(page.locator('.link-card img').first).to_be_attached();expect(page.locator('.link-card img').first).to_have_js_property('complete',True)
                    assert page.locator('.link-card').first.bounding_box()['height']==old_height==112
                    assert abs(row.bounding_box()['y']-old_y)<2
                    row.screenshot(path=str(out/f'preview-loaded-{width}.png'))
                    # Hold the POST to observe the immediate pressed state before saving.
                    held=[];page.route('**/api/items/*/react?*',lambda route:held.append(route));bookmark=row.locator('.bookmark-control');bookmark.scroll_into_view_if_needed();start_lists=len(lists)
                    page.evaluate('(id)=>window.readingRow=document.querySelector(`[data-id="${id}"]`)',target_id)
                    bookmark.click();expect(bookmark).to_have_attribute('aria-pressed','true');expect(bookmark).to_be_disabled();assert held
                    assert not store.item(target_id)['saved'];held.pop().continue_();expect(bookmark).to_be_enabled();assert store.item(target_id)['saved']
                    assert len(lists)==start_lists
                    assert page.locator('.news').evaluate_all('(rows)=>rows.map(row=>row.dataset.id)')==ids
                    assert page.evaluate('(id)=>window.readingRow===document.querySelector(`[data-id="${id}"]`)',target_id)
                    read=row.locator('.read-control');read.click();expect(read).to_have_attribute('aria-pressed','true');assert held;held.pop().continue_();expect(read).to_be_enabled();assert store.item(target_id)['read']
                    assert len(lists)==start_lists;expect(page.locator('.news')).to_have_count(120)
                    page.unroute('**/api/items/*/react?*')
                    # Failed save restores the pressed state, keeps the row and reading position.
                    anchor_y=row.bounding_box()['y'];page.route('**/api/items/*/react?*',lambda route:route.fulfill(status=503,content_type='application/json',body='{"error":"fixture save failure"}'))
                    bookmark.click();expect(bookmark).to_have_attribute('aria-pressed','true');expect(bookmark).to_be_enabled();assert store.item(target_id)['saved'];assert abs(row.bounding_box()['y']-anchor_y)<2
                    page.unroute('**/api/items/*/react?*')
                    # Unread filter removes only the confirmed row; loaded paging is not reset.
                    select(unread);expect(page.locator('.news')).to_have_count(50);first=page.locator('.news').first;removed=first.get_attribute('data-id');n=len(lists)
                    first.locator('.read-control').click();expect(page.locator(f'.news[data-id="{removed}"]')).to_have_count(0);expect(page.locator('.news')).to_have_count(49);assert len(lists)==n
                    select(saved);expect(page.locator('.news')).to_have_count(1);page.locator('.bookmark-control').click();expect(page.locator('.news')).to_have_count(0);expect(page.locator('#feed')).to_contain_text('条件に合う記事がありません')
                    # The existing settings option really disables previews and survives reload.
                    select(any_view);page.locator('#settings-open').click();expect(page.locator('#settings')).to_be_visible();page.locator('#preview-form [name=show_link_previews]').uncheck();page.locator('#preview-form').get_by_role('button',name='リンクカードの設定を保存').click()
                    expect(page.locator('#preview-message')).to_contain_text('保存しました');assert store.settings()['show_link_previews'] is False
                    page.locator('#settings-close').click();expect(page.locator('#feed .link-card')).to_have_count(0)
                    page.reload();expect(page.locator('#feed')).to_have_attribute('aria-busy','false');expect(page.locator('#feed .link-card')).to_have_count(0)
                    restored=page.locator('.view-row').evaluate_all('(rows)=>rows.map(row=>row.dataset.viewId)');assert restored==[item['id'] for item in store.custom_filters()]
                    page.screenshot(path=str(out/f'reader-{width}.png'))
                    assert not errors,errors
                    results.append({'width':width,'pointer_order_and_failure':True,'keyboard_order':True,'preview_no_layout_shift':True,'optimistic_reactions_with_rollback':True,'no_list_request_or_paging_reset':True,'filter_membership':True,'preview_setting_persisted':True})
                finally:context.close();server.shutdown();server.server_close();worker.join();store.close()
        browser.close()
    (out/'browser-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
