"""Chromium + WebKit bookmarklet handoff, confirmation and existing saving API.
Run: uv run --offline --with playwright python tests/bookmarklet_browser.py OUTPUT
Only temporary DB and mocked AI are used; no real Notion writes.
"""
import argparse
import html
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock
from playwright.sync_api import sync_playwright, expect
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from xangi_feed.server import Server
from xangi_feed.store import Store
from browser_check import FixtureProxy, TOKEN, PREFIX
TITLE='日本語の記事 <img src=x onerror=alert(1)> と資料'


def run(browser_type,width,output):
    checks=[];posts=[]
    with tempfile.TemporaryDirectory() as tmp:
        store=Store(Path(tmp)/'workspace/.xangi-feed/feed.db',seed=False)
        host=Mock(configured=True);host.create.return_value='save-browser-session';host.agents.return_value=[]
        server=Server(store,TOKEN,host=host);server.RequestHandlerClass=FixtureProxy
        def start(item_id):store.article_fetch_state(item_id,'done');return store.item(item_id)
        server.articles.start=start
        threading.Thread(target=server.serve_forever,daemon=True).start()
        origin=f'http://127.0.0.1:{server.server_port}';base=origin+PREFIX
        try: browser=browser_type.launch(headless=True)
        except Exception:
            server.shutdown();server.server_close();store.close();raise
        context=browser.new_context(viewport={'width':width,'height':860})
        drop={'next_save':False}
        def auth(route):
            headers={**route.request.headers,'Authorization':'Bearer '+server.token}
            if route.request.method=='POST':posts.append(route.request.url)
            if drop['next_save'] and route.request.url.endswith('/save'):
                drop['next_save']=False
                response=route.fetch(headers=headers)
                assert response.ok
                route.abort();return
            route.continue_(headers=headers)
        context.route(origin+'/**',auth)
        context.route('https://example.org/**',lambda route:route.fulfill(status=200,content_type='text/html; charset=utf-8',body='<title>'+html.escape(TITLE)+'</title><h1>Article</h1>'))
        page=context.new_page()
        try:
            page.goto(base+'capture')
            code=page.locator('#code').input_value();assert code.startswith('javascript:')
            page.goto(origin+'/api/extensions/xangi-feed/ui');page.wait_for_url(base)
            page.locator('#settings-open').click();expect(page.locator('#settings')).to_be_visible()
            expect(page.locator('#settings-bookmarklet-code')).to_have_value(code)
            expect(page.locator('#settings-bookmarklet-copy')).to_be_visible()
            page.locator('#settings-bookmarklet-copy').click()
            expect(page.locator('#settings-bookmarklet-message')).to_contain_text('コピー')
            page.locator('#bookmarklet-settings').screenshot(path=str(output/f'{browser_type.name}-{width}-settings.png'))
            checks.append('Normal UI entry still opens feed; settings contains the same registration code and copy action')
            assert not posts
            page.goto('https://example.org/article?q=a%26b#section')
            page.evaluate('(url)=>{location.href=url}',base+'capture#url=https%3A%2F%2Fexample.org%2Farticle')
            page.wait_for_url(base+'capture*')
            expect(page.locator('body')).to_contain_text('cross-origin extension requests are not allowed')
            assert not posts
            checks.append('Host proxy still rejects direct cross-site service navigation')
            page.goto('https://example.org/article?q=a%26b#section')
            page.evaluate('(code)=>{location.href=code}',code)
            page.wait_for_url(base+'capture')
            expect(page.locator('#title')).to_have_value(TITLE)
            expect(page.locator('#url')).to_have_value('https://example.org/article?q=a%26b#section')
            assert not posts and not page.locator('#result').is_visible()
            assert page.evaluate('document.querySelectorAll("img").length')==0
            checks.append('Actual javascript URL handoff preserves encoded URL and treats title as plain text; no auto POST')
            page.locator('#add').click();expect(page.locator('#status')).to_have_text('xangi-feedに追加済み')
            assert host.send.call_count==0 and len(store.list_items('manual')['items'])==1
            checks.append('Add-only confirms explicitly and never requests Notion')
            page.reload();expect(page.locator('#url')).to_have_value('https://example.org/article?q=a%26b#section')
            host.configured=False
            page.locator('#save').click();expect(page.locator('#status')).to_have_text('操作を完了できませんでした')
            assert host.send.call_count==0
            host.configured=True
            checks.append('Explicit API rejection permits user retry after AI setup is fixed')
            page.locator('#save').click();expect(page.locator('#status')).to_have_text('Notion保存を依頼済み（処理中）')
            assert host.send.call_count==1 and len(store.list_items('manual')['items'])==1
            checks.append('Add + save reuses canonical item and delegates to existing user saving policy')
            page.reload();expect(page.locator('#status')).to_have_text('Notion保存を依頼済み（処理中）')
            count=len(posts);page.locator('#save').click();expect(page.locator('#status')).to_have_text('Notion保存を依頼済み（処理中）')
            assert len(posts)==count and host.send.call_count==1
            checks.append('Reload retains draft and request ID; pending click uses GET without another save')
            item=store.list_items('manual')['items'][0];save=store.item(item['id'])['save_request']
            def report(status,summary):server.saving.report(item['id'],{'request_id':save['request_id'],'session_id':save['session_id'],'status':status,'page_url':'https://www.notion.so/'+'1'*32,'summary':summary})
            report('partial','動画のみ保存。文字起こしは未保存。')
            page.locator('#capture-refresh').click();expect(page.locator('#status')).to_have_text('Notionに一部保存');expect(page.locator('#notion')).to_be_visible()
            checks.append('Partial result reports missing content and actual page link')
            report('saved','動画と文字起こしをDBに保存・照合済み。')
            page.locator('#capture-refresh').click();expect(page.locator('#status')).to_have_text('Notion保存完了')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output/f'{browser_type.name}-{width}-saved.png'),full_page=True)
            checks.append('Saved callback + readable layout without horizontal overflow')
            drop['next_save']=True
            page.locator('#save').click();expect(page.locator('#status')).to_have_text('依頼結果を確認してください')
            assert host.send.call_count==2
            page.reload();expect(page.locator('#status')).to_have_text('依頼結果を確認してください')
            count=len(posts);page.locator('#capture-refresh').click();expect(page.locator('#status')).to_have_text('Notion保存を依頼済み（処理中）')
            assert len(posts)==count and host.send.call_count==2
            checks.append('Lost POST response + reload recovers same request via GET; no automatic resend')
            before=len(posts)
            page.goto(base+'capture#url=javascript%3Aalert(1)&save=true')
            expect(page.locator('#error')).to_contain_text('HTTP/HTTPS')
            assert len(posts)==before
            checks.append('Invalid shared URL rejected; action flags cannot cause writes')
            page.goto(base+'capture#url=https%3A%2F%2Fexample.org%2Fnew&title=New&save=true&confirm=true')
            expect(page.locator('#url')).to_have_value('https://example.org/new')
            assert len(posts)==before
            checks.append('Fresh share replaces previous draft but never submits from URL parameters')
        finally:
            context.close();browser.close();server.shutdown();server.server_close();store.close()
    return {'engine':browser_type.name,'width':width,'checks':checks}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--engine',choices=['all','chromium','webkit'],default='all')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    results=[]
    try:
        with sync_playwright() as p:
            runs=[]
            if args.engine in ['all','chromium']:runs.extend([(p.chromium,1280),(p.chromium,390)])
            if args.engine in ['all','webkit']:runs.append((p.webkit,390))
            for engine,width in runs:
                result=run(engine,width,args.output);results.append(result)
                print(engine.name,width,':',len(result['checks']),'checks passed',flush=True)
    finally:
        (args.output/'browser-results.json').write_text(json.dumps({'requested_engine':args.engine,'runs':results,'limits':['Real iPhone bookmark registration and execution not tested.','AI/Notion writes are mocked.']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
