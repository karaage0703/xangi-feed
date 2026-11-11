"""Each article tab owns its classification, including blocked browser storage."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0,os.environ.get('FEED_TEST_REPO',str(Path(__file__).resolve().parent.parent)))
from playwright.sync_api import expect,sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store


def main():
    out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);results=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/snap/bin/chromium'))
        for width in [1280,393,360]:
            with tempfile.TemporaryDirectory() as tmp:
                store=Store(Path(tmp)/'db',seed=False);store.save_settings({'enabled':False})
                robot=store.source(store.add_source('工作ブログ','https://robot.example/feed','blog'))
                science=store.source(store.add_source('科学フィード','https://science.example/feed','paper'))
                for source,title,topic in [(robot,'工作の記事','ロボット'),(science,'科学の記事','科学')]:
                    store.ingest(source,[{'url':source['url']+'/article','title':title,'summary':'分類保存の確認','topics':[topic],'published':None}])
                a=next(i for i in store.list_items(tab='all')['items'] if i['title']=='工作の記事')
                b=next(i for i in store.list_items(tab='all')['items'] if i['title']=='科学の記事')
                c,_=store.add_article({'url':'https://video.example/post','title':'映像の記事'})
                store.save_article_content(c['id'],{'kind':'video','body':'映像の本文'})
                for item,tag in [(a,'工作'),(b,'科学ノート'),(c,'映像')]:store.change_tag(item['id'],'add',tag)
                for item in [a,b]:store.react(item['id'],{'saved':True});store.save_note(item['id'],'メモ')
                store.react(b['id'],{'read':True});store.discussion(b['id'],'sent','fixture-session')
                server=Server(store,'fixture');thread=threading.Thread(target=server.serve_forever);thread.start()
                origin=f'http://127.0.0.1:{server.server_port}'
                context=browser.new_context(viewport={'width':width,'height':1000},extra_http_headers={'Authorization':'Bearer fixture'})
                page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                def tab(name):
                    page.locator(f'[data-tab={name}]').click()
                    expect(page.locator(f'[data-tab={name}]')).to_have_attribute('aria-pressed','true')
                def panel(opened):
                    if width<901:page.locator('#classification-open' if opened else '#classification-done').click()
                def filters(kind,topic,tag_name,source,search):
                    panel(True)
                    page.locator(f'#kind-choices [data-kind={kind}]').click()
                    if topic:page.locator(f'#topic-choices [data-topic="{topic}"]').click()
                    if tag_name:page.locator(f'#manual-tag-choices [data-tag="{tag_name}"]').click()
                    page.locator('#source-search').fill(search if source else '')
                    if source:page.locator('#source').select_option(str(source['id']))
                    panel(False);page.locator('#search').fill(search)
                    expect(page.locator('#active-filters')).to_contain_text(search)
                try:
                    page.goto(origin);expect(page.locator('.news')).to_have_count(2)
                    filters('blog','ロボット','工作',robot,'工作')
                    expect(page.locator('.news')).to_have_count(1)
                    tab('all');expect(page.locator('#active-filters')).to_be_empty();expect(page.locator('.news')).to_have_count(3)
                    filters('paper','科学','科学ノート',science,'科学')
                    expect(page.locator('.news')).to_have_count(1)
                    tab('manual');expect(page.locator('#active-filters')).to_be_empty()
                    filters('video','', '映像',None,'映像')
                    expect(page.locator('.news')).to_have_count(1)
                    for name in ['saved','notes','read','discussed','muted']:
                        tab(name);expect(page.locator('#active-filters')).to_be_empty()
                        page.locator('#search').fill(name+'条件')
                        expect(page.locator('#active-filters')).to_contain_text(name+'条件')
                    tab('recommended');expect(page.locator('#search')).to_have_value('工作')
                    expect(page.locator('#active-filters')).to_contain_text('自分のタグ: 工作')
                    tab('discovery');tab('all')
                    expect(page.locator('#search')).to_have_value('科学')
                    expect(page.locator('#active-filters')).to_contain_text('論文')
                    tab('manual');expect(page.locator('#search')).to_have_value('映像')
                    expect(page.locator('#active-filters')).to_contain_text('YouTube')
                    for name in ['saved','notes','read','discussed','muted']:
                        tab(name);expect(page.locator('#search')).to_have_value(name+'条件')
                    page.reload();expect(page.locator('#search')).to_have_value('工作')
                    panel(True);expect(page.locator('#source-search')).to_have_value('工作');panel(False)
                    tab('all');page.get_by_role('button',name='すべて解除',exact=True).click()
                    expect(page.locator('.news')).to_have_count(3)
                    tab('recommended');expect(page.locator('#search')).to_have_value('工作')
                    page.goto('about:blank');page.goto(origin)
                    expect(page.locator('#active-filters')).to_contain_text('ロボット')
                    tab('all');expect(page.locator('#active-filters')).to_be_empty()
                    tab('manual');expect(page.locator('#active-filters')).to_contain_text('自分のタグ: 映像')
                    page.screenshot(path=str(out/f'{width}-manual.png'),full_page=True)
                    # Removed labels/sources must not resurrect from another tab's storage.
                    page.request.post(origin+'/api/tags/delete',data={'name':'工作','confirm':True})
                    page.request.post(origin+f'/api/sources/{robot["id"]}/delete',data={'confirm':True})
                    page.reload()
                    expect(page.locator('#active-filters')).not_to_contain_text('自分のタグ: 工作')
                    expect(page.locator('#source')).to_have_value('')
                    for bad in ['{broken',json.dumps({'kinds':['unknown',1],'topics':['unknown'],'tags':['deleted'],'source':'999999'})]:
                        page.evaluate('(value)=>localStorage.setItem("xangi-feed-classification:all",value)',bad)
                        page.reload();tab('all');expect(page.locator('#active-filters')).to_be_empty()
                        tab('manual');expect(page.locator('#active-filters')).to_contain_text('映像')
                    page.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new Error('blocked')}})")
                    page.reload()
                    panel(True);page.locator('#kind-choices [data-kind=blog]').click();panel(False)
                    tab('all');expect(page.locator('#active-filters')).to_be_empty()
                    panel(True);page.locator('#kind-choices [data-kind=paper]').click();panel(False)
                    tab('recommended');expect(page.locator('#active-filters')).to_contain_text('ブログ')
                    tab('all');expect(page.locator('#active-filters')).to_contain_text('論文')
                    assert not errors,errors
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    results.append({'width':width,'eight_tabs_independent':True,'reload_reopen':True,'clear_only_current':True,'removed_labels_sources_pruned':True,'invalid_and_blocked_storage':True,'errors':errors})
                finally:
                    context.close();server.shutdown();server.server_close();thread.join();store.close()
        browser.close()
    (out/'result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results,ensure_ascii=False))

if __name__=='__main__':main()
