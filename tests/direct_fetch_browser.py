"""Offline browser regression for an existing URL-only YouTube item."""
import json
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect,sync_playwright
from xangi_feed.store import Store
from xangi_feed.server import Server

VIDEO='F55l_MtWP-g'
URL='https://youtu.be/'+VIDEO+'?si=fixture'


def main():
    output=Path(sys.argv[1]);output.mkdir(parents=True,exist_ok=True)
    result=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True)
        for width in [1280,390]:
            with tempfile.TemporaryDirectory() as tmp:
                store=Store(Path(tmp)/'fixture.db',seed=False)
                store.save_settings({'enabled':False})
                old,_=store.add_article({'url':URL})
                store.save_note(old['id'],'残すメモ');store.react(old['id'],{'saved':True,'rating':1})
                server=Server(store,'fixture',host=SimpleNamespace(configured=False,agent_id=''))
                metadata={'videoId':VIDEO,'title':'YouTubeの動画タイトル','shortDescription':'動画の説明欄'}
                server.articles.fetcher=lambda url:('<script>var p={"videoDetails":'+json.dumps(metadata)+'};</script><body>footer only</body>').encode()
                def capture(url,path,**kwargs):path.write_text('動画の文字起こし本文です。\n次の段落です。');return 'captions'
                worker=threading.Thread(target=server.serve_forever);worker.start()
                context=browser.new_context(viewport={'width':width,'height':900},extra_http_headers={'Authorization':'Bearer fixture'})
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                try:
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    page.locator('#article-open').click();page.locator('#article-form [name=url]').fill(URL)
                    with patch('xangi_feed.article.youtube',side_effect=capture):
                        page.locator('#article-form button[type=submit]').click()
                        expect(page.locator('#detail')).to_be_visible(timeout=10000)
                        expect(page.locator('#detail-body')).to_contain_text('公開字幕')
                    page.locator('.captured-article > summary').click()
                    expect(page.locator('.captured-body')).to_have_text('動画の文字起こし本文です。\n次の段落です。')
                    expect(page.locator('#detail-body')).not_to_contain_text('footer only')
                    self_item=store.item(old['id'])
                    assert self_item['kind']=='video' and self_item['note']=='残すメモ'
                    assert self_item['saved']==1 and self_item['rating']==1
                    assert store.list_items(tab='all')['total']==1
                    page.screenshot(path=str(output/f'captured-{width}.png'))
                    def fail(url):raise TimeoutError('fixture private')
                    server.articles.fetcher=fail
                    page.get_by_role('button',name='本文を再取得',exact=True).click()
                    expect(page.locator('#detail-body .source-error')).to_contain_text('本文を取得できませんでした',timeout=10000)
                    page.reload();page.locator('.news').get_by_role('button',name='メモを編集',exact=True).click()
                    expect(page.locator('#detail-body')).to_contain_text('公開字幕')
                    assert not errors,errors
                    result.append({'width':width,'legacy_item_backfilled':True,'video_type':True,'transcript_body':True,'user_data_preserved':True,'retry_failure_visible':True,'reload_preserved':True,'page_errors':errors})
                finally:
                    context.close();server.shutdown();server.server_close();worker.join();store.close()
        browser.close()
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
