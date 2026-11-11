"""Top recommendations and popup-free PWA article links with fixtures only."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0, os.environ.get('FEED_TEST_REPO', str(Path(__file__).resolve().parent.parent)))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.store import Store
from xangi_feed.server import Server


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / 'fixture.db', seed=False)
        ids=[]
        for i in range(3):
            item,_=store.add_article({'url':f'https://example.org/{i}','title':f'表示確認の記事 {i}'})
            ids.append(item['id'])
            if i<2:
                store.save_analysis(item['id'],store.content_hash(item),'done',{'title_ja':item['title'],'summary_ja':'表示確認の要約','comment':'表示確認のコメント','reason':'検証用'})
        original_hashes={item:store.content_hash(store.item(item)) for item in ids}
        store.db.execute('UPDATE items SET topics=? WHERE id=?', (json.dumps(['ロボット']), ids[1])); store.db.commit()
        store.react(ids[1], {'rating': 1})
        server=Server(store,'fixture-token-'*4)
        thread=threading.Thread(target=server.serve_forever); thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        try:
            checks=[]
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
                for label,width,pwa in [('desktop',1280,False),('mobile',393,True),('small-mobile',360,True),('display-mode-pwa',393,True)]:
                    store.save_settings({'mark_read_on_open':False,'recommendation_min_preference':None,'recommendation_days':0})
                    context=browser.new_context(viewport={'width':width,'height':900},extra_http_headers={'Authorization':'Bearer '+'fixture-token-'*4})
                    if pwa:
                        pwa_script = """Object.defineProperty(navigator,'standalone',{value:true,configurable:true}); window.shared=[]; Object.defineProperty(navigator,'share',{configurable:true,value:async data=>{window.shared.push(data)}}); Object.defineProperty(navigator,'clipboard',{value:{writeText:async()=>{throw new Error('blocked')}}}); window.open=()=>{throw new Error('new window forbidden')};"""
                    if label == 'display-mode-pwa':
                        pwa_script += """Object.defineProperty(navigator,'standalone',{value:false}); const realMatchMedia=window.matchMedia.bind(window); window.matchMedia=query=>{const result=realMatchMedia(query);if(query==='(display-mode: standalone)')Object.defineProperty(result,'matches',{value:true});return result;};"""
                    if pwa: context.add_init_script(pwa_script)
                    context.route('https://example.org/**',lambda route:route.fulfill(content_type='text/html',body='<h1>Fixture original</h1>'))
                    page=context.new_page(); errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(origin)
                    if label == 'display-mode-pwa': assert page.evaluate("navigator.standalone===false && matchMedia('(display-mode: standalone)').matches")
                    expect(page.locator('#recommendation-fields')).to_be_enabled()
                    expect(page.locator('#settings')).not_to_be_visible()
                    expect(page.locator('.news')).to_have_count(3)
                    page.get_by_role('button',name='すべて',exact=True).click()
                    expect(page.locator('#recommendation-panel')).not_to_be_visible()
                    page.get_by_role('button',name='カスタム',exact=True).click()
                    page.locator('#recommendation-toggle > summary').click()
                    form=page.locator('#recommendation-form')
                    form.locator('[name=recommendation_min_preference]').fill('2'); form.locator('[name=recommendation_min_preference]').press('Tab')
                    expect(page.locator('#recommendation-message')).to_have_text('保存して反映しました。')
                    expect(page.locator('[data-tab=recommended]')).to_have_attribute('aria-pressed','true')
                    expect(page.locator('.news')).to_have_count(1)
                    expect(form.locator('[name=recommendation_priority]')).to_be_visible()
                    page.screenshot(path=str(output/f'{label}-top.png'),full_page=True)
                    page.reload()
                    expect(form.locator('[name=recommendation_min_preference]')).to_have_value('2')
                    expect(page.locator('.news')).to_have_count(1)
                    title=page.locator('.news-title')
                    # A native anchor opens separately in all modes, with no chooser.
                    original_url = page.url
                    with context.expect_page(timeout=3000) as popup:
                        title.click()
                    popup.value.wait_for_load_state()
                    assert popup.value.url == 'https://example.org/1'
                    assert page.url == original_url
                    expect(page.locator('#article-link')).not_to_be_visible()
                    popup.value.close()
                    if pwa:
                        row = page.locator('.news').first
                        row.locator('.more-actions summary').click()
                        row.get_by_role('button', name='記事の詳細', exact=True).click()
                        with context.expect_page(timeout=3000) as original:
                            page.locator('#detail-body').get_by_role('link', name='原文を読む', exact=True).click()
                        original.value.wait_for_load_state()
                        assert original.value.url == 'https://example.org/1'
                        expect(page.locator('#detail')).to_be_visible()
                        assert page.url == original_url
                        original.value.close(); page.locator('#detail-close').click()
                        row.locator('.more-actions summary').click()
                        row.get_by_role('button', name='リンクを共有・コピー', exact=True).click()
                        expect(page.locator('#article-link')).to_be_visible()
                        page.locator('#article-link-share').click()
                        expect(page.locator('#article-link-message')).to_have_text('共有しました。')
                        assert page.evaluate('window.shared[0].url')=='https://example.org/1'
                        page.locator('#article-link-copy').click()
                        expect(page.locator('#article-link-copy-fallback')).to_be_visible()
                        expect(page.locator('#article-link-url')).to_have_value('https://example.org/1')
                        page.locator('#article-link').screenshot(path=str(output/f'{label}-link.png'))
                        with context.expect_page(timeout=3000) as alternate:
                            page.locator('#article-link-open').click()
                        alternate.value.wait_for_load_state()
                        assert alternate.value.url == 'https://example.org/1'
                        assert page.url == original_url
                        alternate.value.close()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert all(store.content_hash(store.item(item))==original_hashes[item] for item in ids)
                    assert not errors,errors
                    checks.append({'viewport':label,'custom_conditions_autosave_reload':True,'pwa_mode':pwa,'article_link_ok':True,'console_errors':errors})
                    context.close()
                browser.close()
            (output/'result.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
            print(json.dumps({'result':'passed','checks':checks},ensure_ascii=False))
        finally:
            server.shutdown();server.server_close();thread.join();server.collector.close();server.enricher.close();store.close()


if __name__=='__main__':main()
