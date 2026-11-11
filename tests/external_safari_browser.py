"""Check iOS Safari URI handoff, not an actual Safari launch."""
import json,sys,tempfile,threading,os
from pathlib import Path
sys.path.insert(0,os.environ.get('FEED_TEST_REPO',str(Path(__file__).resolve().parent.parent)))
from playwright.sync_api import sync_playwright,expect
from xangi_feed.store import Store
from xangi_feed.server import Server
IPHONE='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'
IPAD='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'


def main():
    output=Path(sys.argv[1]);output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store=Store(Path(tmp)/'fixture.db',seed=False)
        item=store.add_article({'url':'https://example.org/article?lang=ja&q=a%26b','title':'外部Safariで読む記事'})[0]
        item_id=item['id'];original_url=item['url'];expected='x-safari-https:'+original_url[len('https:'):]
        server=Server(store,'fixture-token-'*4);thread=threading.Thread(target=server.serve_forever);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        try:
            results=[]
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                for label,ua,width,standalone in [('iphone-pwa',IPHONE,393,True),('ipad-pwa',IPAD,768,True),('iphone-browser',IPHONE,393,False),('desktop','fixture desktop',1280,True),('android-pwa','Mozilla/5.0 (Linux; Android 15) Chrome/140 Mobile Safari/537.36',393,True)]:
                    store.react(item_id,{'read':False});store.save_settings({'mark_read_on_open':True})
                    context=browser.new_context(viewport={'width':width,'height':900},user_agent=ua,has_touch=width<900,extra_http_headers={'Authorization':'Bearer '+'fixture-token-'*4})
                    context.add_init_script("Object.defineProperty(navigator,'standalone',{value:"+str(standalone).lower()+"});"+("Object.defineProperty(navigator,'platform',{value:'MacIntel'});Object.defineProperty(navigator,'maxTouchPoints',{value:5});" if label=='ipad-pwa' else '')+"window.handoffs=[];document.addEventListener('click',event=>{const link=event.target.closest('a');if(link?.getAttribute('href')?.startsWith('x-safari-https:')){window.handoffs.push(link.getAttribute('href'));event.preventDefault();}});window.open=()=>{throw new Error('JS popup forbidden')};")
                    context.route('https://example.org/**',lambda route:route.fulfill(content_type='text/html',body='<h1>Fixture original</h1>'))
                    page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(origin);expect(page.locator('.news')).to_have_count(1)
                    target=page.locator('.news-title');ios_pwa=label in {'iphone-pwa','ipad-pwa'}
                    if ios_pwa:
                        expect(target).to_have_attribute('href',original_url)
                        expect(target).to_have_attribute('target','_blank')
                        page.locator('#settings-open').click()
                        expect(page.locator('#ios-external-safari')).not_to_be_checked()
                        expect(page.locator('#ios-link-settings')).not_to_contain_text('試験')
                        page.locator('#ios-external-safari').check()
                        page.locator('#settings-close').click()
                        expect(target).to_have_attribute('href',expected);expect(target).to_have_attribute('target','_self')
                        assert page.evaluate("safariAppURL('http://example.org/')") is None
                        assert page.evaluate("safariAppURL('javascript:alert(1)')") is None
                        assert page.evaluate("safariAppURL('https://user:password@example.org/')") is None
                        before=page.url
                        with page.expect_response(lambda response:response.url.endswith('/open')):target.click()
                        expect(page.locator('.news')).to_have_count(0)
                        assert page.evaluate('window.handoffs')==[expected];assert page.url==before
                        assert store.item(item_id)['read']
                        page.get_by_role('button',name='すべて',exact=True).click()
                        page.locator('.news .more-actions summary').click()
                        page.get_by_role('button',name='リンクを共有・コピー',exact=True).click()
                        expect(page.locator('#article-link-open')).to_have_attribute('href',expected)
                        expect(page.locator('#article-link-open')).to_have_attribute('target','_self')
                        expect(page.locator('#article-link-url')).to_have_value(original_url)
                        page.locator('#article-link-close').click()
                        page.locator('#settings-open').click()
                        expect(page.locator('#ios-external-safari')).to_be_checked()
                        page.locator('#ios-link-settings').screenshot(path=str(output/f'{label}-option.png'))
                        page.locator('#ios-external-safari').uncheck()
                        expect(page.locator('#ios-link-status')).to_have_text('この端末に保存しました。')
                        page.locator('#settings-close').click();store.react(item_id,{'read':False});page.reload()
                        expect(target).to_have_attribute('href',original_url);expect(target).to_have_attribute('target','_blank')
                        with context.expect_page(timeout=5000) as opened:target.click()
                        opened.value.wait_for_load_state();assert opened.value.url==original_url;opened.value.close()
                        page.locator('#settings-open').click();expect(page.locator('#ios-external-safari')).not_to_be_checked()
                        page.locator('#ios-external-safari').check();page.locator('#settings-close').click()
                        store.react(item_id,{'read':False});page.reload();target=page.locator('.news-title')
                        expect(target).to_have_attribute('href',expected)
                        store.save_settings({'mark_read_on_open':False})
                        with page.expect_response(lambda response:response.url.endswith('/open')):target.click()
                        assert not store.item(item_id)['read']
                        page.evaluate("localStorage.setItem('xangi-feed-ios-external-safari','unknown')")
                        page.reload()
                        expect(target).to_have_attribute('href',original_url)
                        page.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new Error('blocked')}})")
                        page.reload()
                        expect(target).to_have_attribute('href',original_url)
                        page.locator('#settings-open').click()
                        expect(page.locator('#ios-external-safari')).not_to_be_checked()
                    else:
                        expect(target).to_have_attribute('href',original_url);expect(target).to_have_attribute('target','_blank')
                        with context.expect_page(timeout=5000) as opened:target.click()
                        opened.value.wait_for_load_state();opened.value.close()
                        page.locator('#settings-open').click();expect(page.locator('#ios-link-settings')).not_to_be_visible()
                    assert not errors,errors
                    results.append({'environment':label,'ios_pwa':ios_pwa,'safari_uri_handoff_simulated':ios_pwa,'default_off':True,'explicit_on_off_persisted':ios_pwa,'invalid_or_blocked_storage_off':ios_pwa,'native_app_launch_verified':False,'console_errors':errors})
                    context.close()
                browser.close()
            (output/'result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps({'result':'passed','checks':results},ensure_ascii=False))
        finally:
            server.shutdown();server.server_close();thread.join();server.collector.close();server.enricher.close();store.close()


if __name__=='__main__':main()
