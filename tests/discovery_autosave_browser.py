"""Automatic search settings: IME, debounce, errors, latest-wins and navigation."""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect,sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store


def wait_for_prompt(page, expected, timeout=15):
    from urllib.parse import urljoin
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=page.request.get(urljoin(page.url,'api/discovery')).json()
        if state.get('result_prompt')==expected and not state['running']:return
        page.wait_for_timeout(50)
    raise AssertionError('latest prompt did not complete: '+repr(state))


def main():
    out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);results=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/snap/bin/chromium'))
        for width in [1280,390,360]:
            with tempfile.TemporaryDirectory() as tmp:
                store=Store(Path(tmp)/'db',seed=False)
                item,_=store.add_article({'url':'https://known.example/robot','title':'ロボットの実験'})
                store.react(item['id'],{'rating':1});store.save_settings({'enabled':False,'discovery_enabled':False})
                server=Server(store,'fixture',host=Mock(configured=False))
                candidate={'id':'candidate','url':'https://new.example/article','feed_url':'https://new.example/feed','title':'ロボットの実験手順を公開しました','source_name':'ロボット実験室','published':time.time(),'verified_at':time.time(),'matched_topics':['ロボット'],'reason':'実装例','language':'ja'}
                server.discovery.searcher=Mock(return_value=([candidate],{'searches_ok':1}))
                thread=threading.Thread(target=server.serve_forever);thread.start()
                context=browser.new_context(viewport={'width':width,'height':1000},extra_http_headers={'Authorization':'Bearer fixture'})
                page=context.new_page();errors=[];posts=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda r:posts.append(r.post_data_json) if r.method=='POST' and r.url.endswith('/api/settings') else None)
                try:
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    page.get_by_role('button',name='おすすめ',exact=True).click()
                    field=page.locator('[name=discovery_prompt]');status=page.locator('#discovery-save-message')
                    expect(field).to_be_enabled()
                    expect(page.get_by_role('button',name='保存して検索',exact=True)).to_have_count(0)
                    expect(page.get_by_role('button',name='今すぐ探す',exact=True)).to_be_enabled()
                    field.dispatch_event('compositionstart');field.fill('日本語')
                    page.wait_for_timeout(800);assert posts==[]
                    field.dispatch_event('compositionend')
                    expect(status).to_have_text('自動保存して反映しました。')
                    expect(page.locator('.discovery-card')).to_have_count(1)
                    assert len(posts)==1
                    field.fill('日本語の');page.wait_for_timeout(100)
                    field.fill('日本語の記事');page.wait_for_timeout(100)
                    field.fill('日本語の記事だけ探してください。')
                    wait_for_prompt(page,'日本語の記事だけ探してください。')
                    assert len(posts)==2,repr(posts)
                    failures=[True]
                    def fail_once(route):
                        if route.request.method=='POST' and failures[0]: failures[0]=False;route.abort()
                        else:route.continue_()
                    page.route('**/api/settings',fail_once)
                    field.fill('日本語のみ。直近7日')
                    expect(status).to_contain_text('保存できませんでした')
                    expect(field).to_have_value('日本語のみ。直近7日')
                    assert store.settings()['discovery_prompt']=='日本語の記事だけ探してください。'
                    page.get_by_role('button',name='保存を再試行',exact=True).click()
                    expect(status).to_have_text('自動保存して反映しました。')
                    assert store.settings()['discovery_prompt']=='日本語のみ。直近7日'
                    page.unroute('**/api/settings')
                    delayed=[True]
                    def slow_first(route):
                        if route.request.method=='POST' and delayed[0]: delayed[0]=False;time.sleep(1.1)
                        route.continue_()
                    page.route('**/api/settings',slow_first)
                    phase=len(posts)
                    page.evaluate("""() => {
                        const f=document.querySelector('[name=discovery_prompt]');
                        function set(v){f.value=v;f.dispatchEvent(new Event('input',{bubbles:true}));}
                        set('日本語のみ。直近2日');
                        setTimeout(()=>set('日本語のみ。直近3日'),800);
                        setTimeout(()=>set('日本語のみ。直近4日'),850);
                        setTimeout(()=>document.querySelector('[data-tab=all]').click(),900);
                    }""")
                    wait_for_prompt(page,'日本語のみ。直近4日')
                    expect(page.locator('[data-tab=all]')).to_have_attribute('aria-pressed','true')
                    assert [p['discovery_prompt'] for p in posts[phase:]]==['日本語のみ。直近2日','日本語のみ。直近4日']
                    page.unroute('**/api/settings')
                    page.reload();page.get_by_role('button',name='おすすめ',exact=True).click()
                    expect(field).to_have_value('日本語のみ。直近4日')
                    before=len(posts);interval=page.locator('[name=discovery_interval_hours]');interval.fill('1');interval.press('Tab')
                    expect(status).to_contain_text('まだ保存していません');assert len(posts)==before
                    interval.fill('24');interval.press('Tab')
                    expect(status).to_have_text('自動保存済みです。')
                    # Unchanged conditions must still start a new search; auto-save used to remove this path.
                    button=page.get_by_role('button',name='今すぐ探す',exact=True)
                    count=server.discovery.searcher.call_count;before=len(posts)
                    button.click()
                    expect(status).to_have_text('検索をリクエストしました。')
                    wait_for_prompt(page,'日本語のみ。直近4日')
                    assert server.discovery.searcher.call_count==count+1
                    assert len(posts)==before
                    # A fresh input is saved and searched immediately, before the debounce expires.
                    count=server.discovery.searcher.call_count
                    field.fill('日本語のみ。直近5日');button.click()
                    expect(status).to_have_text('検索をリクエストしました。')
                    wait_for_prompt(page,'日本語のみ。直近5日')
                    page.wait_for_timeout(800)
                    assert server.discovery.searcher.call_count==count+1
                    assert store.settings()['discovery_prompt']=='日本語のみ。直近5日'
                    # A manual refresh network failure remains retryable without changing the prompt.
                    def fail_refresh(route): route.abort()
                    page.route('**/api/discovery/refresh',fail_refresh)
                    button.click()
                    expect(status).to_contain_text('検索できませんでした')
                    expect(field).to_have_value('日本語のみ。直近5日')
                    page.unroute('**/api/discovery/refresh')
                    count=server.discovery.searcher.call_count
                    page.get_by_role('button',name='検索を再試行',exact=True).click()
                    expect(status).to_have_text('検索をリクエストしました。')
                    wait_for_prompt(page,'日本語のみ。直近5日')
                    assert server.discovery.searcher.call_count==count+1
                    expect(page.locator('#discovery-retry')).to_be_hidden()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert not errors,errors
                    page.screenshot(path=str(out/f'autosave-{width}.png'),full_page=True)
                    results.append({'width':width,'ime_waits':True,'debounced':True,'error_retry_keeps_input':True,'latest_wins':True,'no_tab_switch':True,'reload':True,'invalid_input_not_saved':True,'manual_same_conditions':True,'manual_latest_input':True,'manual_failure_retry':True,'errors':errors})
                finally:
                    context.close();server.shutdown();server.server_close();thread.join();store.close()
        browser.close()
    (out/'result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results,ensure_ascii=False))

if __name__=='__main__':main()
