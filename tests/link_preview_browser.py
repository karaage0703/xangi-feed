"""Fixture-only link card display, preference persistence and normal navigation."""
import base64
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect,sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
# Optional public sample fetched by the evidence run; no remote calls in this test.
png=(out/'public-og-image.bin').read_bytes() if (out/'public-og-image.bin').exists() else base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jK7sAAAAASUVORK5CYII=')
html='<html><meta property="og:title" content="ロボットの音声入力を改善する実装例"><meta property="og:site_name" content="テック記事"><meta property="og:description" content="マイクの配置と雑音の違いを調べた公開記事です。"><meta property="og:image" content="/card.png"></html>'.encode()
with tempfile.TemporaryDirectory() as tmp,sync_playwright() as pw:
    store=Store(Path(tmp)/'db',seed=False)
    store.save_settings({'enabled':False,'discovery_enabled':False})
    first,_=store.add_article({'url':'https://example.org/a','title':'音声入力の実装','summary':'マイクの配置を比較した記事。'})
    second,_=store.add_article({'url':'https://example.org/no-image','title':'画像のない記事'})
    with store.transaction() as db:
        db.execute('UPDATE items SET updated=2000 WHERE id=?',(first['id'],))
        db.execute('UPDATE items SET updated=1000 WHERE id=?',(second['id'],))
    server=Server(store,'fixture-token')
    calls=[]
    def fetch(url,maximum):
        calls.append(url)
        if url.endswith('card.png'):return png
        if url.endswith('no-image'):return b'<html><title>Image-free article</title></html>'
        return html
    server.previews.fetcher=fetch
    worker=threading.Thread(target=server.serve_forever);worker.start()
    browser=pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/snap/bin/chromium'),headless=True)
    results=[]
    try:
        for width in [1280,390,360]:
            store.save_settings({'show_link_previews':True})
            store.react(first['id'],{'read':False})
            context=browser.new_context(viewport={'width':width,'height':600 if width == 1280 else 1000},extra_http_headers={'Authorization':'Bearer fixture-token'})
            context.route('https://example.org/**',lambda route:route.fulfill(body='<h1>Original article</h1>',content_type='text/html'))
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}')
            card=page.locator(f'.link-card[data-item-id="{first["id"]}"]')
            expect(card.locator('img')).to_be_visible(timeout=15000)
            expect(card).to_contain_text('ロボットの音声入力を改善する実装例')
            assert card.locator('img').evaluate('(img) => img.complete && img.naturalWidth > 0')
            assert not store.item(first['id'])['read']
            assert card.bounding_box()['width'] <= 400
            assert card.locator('img').bounding_box()['height'] <= (160 if width == 1280 else 104)
            if width == 1280:
                assert 'https://example.org/no-image' not in calls
                page.set_viewport_size({'width':width,'height':1000})
            page.screenshot(path=str(out/f'link-card-{width}.png'),full_page=True)
            page.locator('#settings-open').click()
            toggle=page.locator('#preview-form [name=show_link_previews]')
            expect(toggle).to_be_checked();toggle.uncheck()
            page.locator('#preview-form button[type=submit]').click()
            expect(page.locator('#preview-message')).to_have_text('リンクカードの設定を保存しました。')
            page.locator('#settings-close').click()
            expect(page.locator('.link-card')).to_have_count(0)
            count=len(calls);page.reload();expect(page.locator('.news')).to_have_count(2)
            expect(page.locator('.link-card')).to_have_count(0);assert len(calls)==count
            page.locator('#settings-open').click();expect(toggle).not_to_be_checked();toggle.check()
            page.locator('#preview-form button[type=submit]').click()
            expect(page.locator('#preview-message')).to_have_text('リンクカードの設定を保存しました。')
            page.locator('#settings-close').click()
            expect(card.locator('img')).to_be_visible()
            other=page.locator(f'.link-card[data-item-id="{second["id"]}"]')
            other.scroll_into_view_if_needed();expect(other).to_contain_text('Image-free article',timeout=15000)
            expect(other.locator('img')).to_have_count(0)
            card.scroll_into_view_if_needed()
            with page.expect_popup() as opened:card.click()
            opened.value.close()
            expect(card).to_have_count(0)
            assert store.item(first['id'])['read']
            assert not errors,errors
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            results.append({'width':width,'default_on':True,'off_persisted_and_no_fetch':True,'fallback':True,'card_click_marks_read':True,'errors':errors})
            context.close()
        (out/'browser-result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    finally:
        browser.close();server.shutdown();server.server_close();worker.join();store.close()
