"""Shared extension chrome, free instructions, and native article-title navigation."""
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
from xangi_feed.server import Server,Handler

PREFIX='/api/extensions/xangi-feed/service'
class EmbeddedHandler(Handler):
    def handle_request(self,mutation):
        self.path=self.path.removeprefix(PREFIX) or '/'
        return super().handle_request(mutation)


def main():
    output=Path(sys.argv[1]);output.mkdir(parents=True,exist_ok=True)
    results=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/snap/bin/chromium'))
        for width in [1280,390,360]:
            with tempfile.TemporaryDirectory() as tmp:
                from xangi_feed.store import Store
                store=Store(Path(tmp)/'db',seed=False)
                liked,_=store.add_article({'url':'https://known.example/robot','title':'ロボットの実験'})
                store.react(liked['id'],{'rating':1})
                store.save_settings({'enabled':False,'discovery_enabled':False})
                server=Server(store,'fixture',host=Mock(configured=False))
                server.RequestHandlerClass=EmbeddedHandler
                article={'id':'real-candidate','title':'ロボットの新しい実験手順を公開','source_name':'実験ラボ','url':'https://article.example/read','feed_url':'https://article.example/rss','reason':'ロボットの実装例があります。','published':time.time(),'verified_at':time.time(),'matched_topics':['ロボット'],'language':'ja'}
                server.discovery.searcher=lambda *a,**kw:([article],{'searches_ok':1,'feeds_checked':1})
                thread=threading.Thread(target=server.serve_forever);thread.start()
                context=browser.new_context(viewport={'width':width,'height':900},extra_http_headers={'Authorization':'Bearer fixture'})
                page=context.new_page();errors=[];posts=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda r:posts.append(r.url) if r.method=='POST' else None)
                try:
                    page.goto(f'http://127.0.0.1:{server.server_port}{PREFIX}/')
                    home=page.get_by_role('link',name='xangiホーム',exact=True)
                    expect(home).to_be_visible();expect(home).to_have_attribute('href','/')
                    expect(home.locator('svg')).to_have_count(1)
                    assert home.bounding_box()['height']>=44
                    page.locator('#theme-select').select_option('dark')
                    expect(page.locator('html')).to_have_attribute('data-theme','dark')
                    page.reload()
                    expect(page.locator('#theme-select')).to_have_value('dark')
                    assert page.evaluate("localStorage.getItem('xangi-theme')")=='dark'
                    page.get_by_role('button',name='おすすめ',exact=True).click()
                    field=page.get_by_role('textbox',name='検索のカスタムプロンプト')
                    field.fill('日本語の記事だけ探してください。')
                    expect(page.locator('#discovery-save-message')).to_have_text('自動保存して反映しました。')
                    expect(page.get_by_role('button',name='保存して検索',exact=True)).to_have_count(0)
                    expect(page.get_by_role('button',name='今すぐ探す',exact=True)).to_have_count(0)
                    expect(page.locator('.discovery-card')).to_have_count(1)
                    expect(page.locator('#discovery-message')).to_contain_text('日本語のみ')
                    expect(page.get_by_role('button',name='配信元に登録',exact=True)).to_have_count(0)
                    title=page.locator('.discovery-card h3 a')
                    expect(title).to_have_attribute('href',article['url'])
                    context.route(article['url'],lambda r:r.fulfill(content_type='text/html; charset=utf-8',body='<h1>原文の記事</h1>'))
                    before=len(posts)
                    with context.expect_page() as popup:title.click()
                    popup.value.wait_for_load_state();expect(popup.value.locator('h1')).to_have_text('原文の記事');popup.value.close()
                    assert len(posts)==before,'article opening must not register or import anything'
                    assert store.sources()==[]
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(output/f'dark-{width}.png'),full_page=True)
                    page.locator('#theme-select').select_option('light')
                    page.screenshot(path=str(output/f'light-{width}.png'),full_page=True)
                    page.reload();page.get_by_role('button',name='おすすめ',exact=True).click()
                    expect(field).to_have_value('日本語の記事だけ探してください。')
                    expect(page.locator('html')).to_have_attribute('data-theme','light')
                    assert not errors,errors
                    results.append({'width':width,'home_svg_44px':True,'theme_shared_and_persisted':True,'free_prompt_persisted':True,'article_title_opens':True,'no_source_registration':True,'errors':errors})
                finally:
                    context.close();server.shutdown();server.server_close();thread.join();store.close()
        browser.close()
    (output/'result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results,ensure_ascii=False))

if __name__=='__main__':main()
