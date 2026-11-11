"""Native links with default auto-read, opt-out, persistence and failure handling."""
import json
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright,expect
from xangi_feed.store import Store
from xangi_feed.server import Server


def main():
    output=Path(sys.argv[1]);output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store=Store(Path(tmp)/'fixture.db',seed=False)
        a=store.add_article({'url':'https://example.org/article','title':'読了動作の記事'})[0]['id']
        b=store.add_article({'url':'https://example.org/video','title':'読了動作の動画'})[0]['id']
        with store.transaction() as db:
            db.execute("UPDATE items SET audio_url='https://example.org/audio.mp3' WHERE id=?",(a,))
            db.execute("UPDATE items SET kind='video' WHERE id=?",(b,))
        server=Server(store,'fixture-token-'*4);thread=threading.Thread(target=server.serve_forever);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        try:
            checks=[]
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                for label,width in [('desktop',1280),('iphone-pwa-model',393),('small-pwa-model',360)]:
                    store.save_settings({'mark_read_on_open':True})
                    store.react(a,{'read':False});store.react(b,{'read':False})
                    context=browser.new_context(viewport={'width':width,'height':900},extra_http_headers={'Authorization':'Bearer '+'fixture-token-'*4})
                    if width<900:context.add_init_script("Object.defineProperty(navigator,'standalone',{value:true});window.open=()=>{throw new Error('JS popup forbidden')}")
                    context.route('https://example.org/**',lambda route:route.fulfill(content_type='text/html',body='<h1>Fixture original</h1>'))
                    page=context.new_page();errors=[];requests=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('request',lambda request:requests.append(request.url) if request.url.endswith('/open') else None)
                    page.goto(origin);expect(page.locator('.news')).to_have_count(2)
                    original=page.url
                    def open_link(locator):
                        with page.expect_response(lambda r:r.url.endswith('/open')) as response:
                            with context.expect_page(timeout=5000) as opened:locator.click()
                        opened.value.wait_for_load_state();assert page.url==original
                        opened.value.close();return response.value
                    row=page.locator(f'.news[data-id="{a}"]')
                    assert open_link(row.locator('.news-title')).status==200
                    expect(row).to_have_count(0)
                    assert store.item(a)['read']
                    expect(page.locator('#notice')).to_contain_text('読了にしました')
                    page.get_by_role('button',name='読了',exact=True).click()
                    expect(page.locator('.news')).to_have_count(1)
                    page.get_by_role('button',name='すべて',exact=True).click()
                    def save_policy(enabled):
                        page.locator('#settings-open').click()
                        field=page.locator('#reading-form [name=mark_read_on_open]')
                        field.set_checked(enabled)
                        with page.expect_response(lambda r:r.url.endswith('/api/settings') and r.request.method=='POST') as saved:
                            page.get_by_role('button',name='読了の設定を保存',exact=True).click()
                        assert saved.value.status==200
                        expect(page.locator('#reading-message')).to_have_text('読了の設定を保存しました。')
                        page.locator('#settings-close').click()
                    save_policy(False);page.reload()
                    page.locator('#settings-open').click()
                    expect(page.locator('#reading-form [name=mark_read_on_open]')).not_to_be_checked()
                    page.locator('#reading-form').screenshot(path=str(output/f'{label}-option-off.png'))
                    page.locator('#settings-close').click()
                    page.get_by_role('button',name='すべて',exact=True).click()
                    video=page.locator(f'.news[data-id="{b}"]')
                    assert open_link(video.locator('.news-title')).status==200
                    assert not store.item(b)['read']
                    video.locator('.more-actions summary').click();video.locator('.read-control').click()
                    expect(video.locator('.read-control')).to_contain_text('未視聴に戻す')
                    assert store.item(b)['read']
                    store.react(b,{'read':False})
                    save_policy(True);page.reload();page.get_by_role('button',name='すべて',exact=True).click()
                    video.locator('.more-actions summary').click();video.get_by_role('button',name='記事の詳細',exact=True).click()
                    expect(page.locator('#detail')).to_be_visible();assert not store.item(b)['read']
                    page.locator('#article-note').fill('まだ保存していないメモ')
                    assert open_link(page.locator('#detail-body').get_by_role('link',name='YouTubeで見る',exact=True)).status==200
                    expect(page.locator('#article-note')).to_have_value('まだ保存していないメモ')
                    assert store.item(b)['read'];page.locator('#detail-close').click()
                    store.react(a,{'read':False});page.reload();page.get_by_role('button',name='すべて',exact=True).click()
                    row=page.locator(f'.news[data-id="{a}"]');row.locator('.more-actions summary').click()
                    row.get_by_role('button',name='リンクを共有・コピー',exact=True).click()
                    before=len(requests);page.locator('#article-link-copy').click()
                    expect(page.locator('#article-link-message')).to_contain_text('コピー')
                    assert len(requests)==before and not store.item(a)['read']
                    assert open_link(page.locator('#article-link-open')).status==200
                    expect(page.locator('#notice')).to_contain_text('読了にしました');assert store.item(a)['read']
                    store.react(a,{'read':False});page.reload();page.get_by_role('button',name='すべて',exact=True).click()
                    row=page.locator(f'.news[data-id="{a}"]');row.locator('.more-actions summary').click()
                    assert open_link(row.get_by_role('link',name='音声を聴く',exact=True)).status==200
                    expect(page.locator('#notice')).to_contain_text('読了にしました');assert store.item(a)['read']
                    store.react(a,{'read':False});page.reload()
                    context.route('**/api/items/'+a+'/open',lambda route:route.fulfill(status=500,json={'error':'fixture failure'}))
                    row=page.locator(f'.news[data-id="{a}"]')
                    assert open_link(row.locator('.news-title')).status==500
                    expect(page.locator('#notice')).to_contain_text('読了を保存できませんでした')
                    expect(row).to_have_count(1);assert not store.item(a)['read']
                    assert not errors,errors
                    checks.append({'viewport':label,'native_window_with_auto_read':True,'off_persisted_manual_video_read':True,'detail_and_draft_preserved':True,'copy_does_not_read':True,'failure_does_not_block_link':True,'console_errors':errors})
                    context.close()
                browser.close()
            (output/'result.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2));print(json.dumps({'result':'passed','checks':checks},ensure_ascii=False))
        finally:
            server.shutdown();server.server_close();thread.join();server.collector.close();server.enricher.close();store.close()


if __name__=='__main__':main()
