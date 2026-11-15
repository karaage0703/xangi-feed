"""Quiet reader chrome and live editorial updates without losing the reading state."""
import json
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect,sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store,RECOMMENDATION_DEFAULTS,VIEW_DEFAULTS


def main():
    out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True);checks=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium-browser')
        for width in [1280,393,360]:
            with tempfile.TemporaryDirectory() as directory:
                store=Store(Path(directory)/'feed',seed=False);store.save_settings({'enabled':False,'show_link_previews':False})
                source=store.source(store.add_source('Fixture podcast','https://example.org/feed','podcast'))
                store.ingest(source,[{'url':f'https://example.org/{n}','title':f'Original {n:03}','summary':'Original excerpt','topics':[],'published':None} for n in range(120)])
                record=store.save_custom_filter({'name':'Podcast','kinds':['podcast'],'recommendations':dict(RECOMMENDATION_DEFAULTS),'conditions':{**VIEW_DEFAULTS,'read':'any','muted':'include'}})
                item=store.list_items(custom_filter=record['id'])['items'][0]
                store.save_analysis(item['id'],store.content_hash(item),'error',error='日本語の解説が必要です: title_ja')
                server=Server(store,'fixture');collector={'running':False,'last_finished':1,'added':0}
                server.collector.status=lambda:dict(collector)
                server.collector.trigger=lambda:False
                worker=threading.Thread(target=server.serve_forever);worker.start()
                context=browser.new_context(viewport={'width':width,'height':900},extra_http_headers={'Authorization':'Bearer fixture'})
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                try:
                    page.goto(f'http://127.0.0.1:{server.server_port}');expect(page.locator('.news')).to_have_count(50)
                    if width<901:page.locator('#classification-open').click()
                    page.locator(f'[data-custom-filter="{record["id"]}"]').click()
                    expect(page.locator('#current-view-title')).to_have_text('Podcast')
                    page.evaluate('poll()')
                    expect(page.locator('#active-view-summary, #view-description')).to_have_count(0)
                    expect(page.locator('body')).not_to_contain_text('title_ja')
                    expect(page.locator('.news').first).to_contain_text('AIが見出しを日本語で生成できませんでした。')
                    refresh=page.locator('#refresh').bounding_box();add=page.locator('#article-open').bounding_box();preferences=page.locator('.header-preferences').bounding_box()
                    assert refresh['height']==add['height']==44
                    assert abs(refresh['y']-add['y'])<1
                    assert abs(add['x']+add['width']-preferences['x']-preferences['width'])<1
                    if width<601:
                        expect(page.locator('#refresh .refresh-icon')).to_be_visible()
                    # Both acquisition controls still work, independently of editorial refresh.
                    with page.expect_request('**/api/refresh'):page.locator('#refresh').click()
                    collector['last_finished']=2;page.evaluate('poll()')
                    page.locator('#article-open').click();expect(page.locator('#article-add')).to_be_visible();page.locator('#article-close').click()
                    page.locator('#more').click();expect(page.locator('.news')).to_have_count(100)
                    page.locator('#more').click();expect(page.locator('.news')).to_have_count(120)
                    ids=page.locator('.news').evaluate_all('(rows)=>rows.map(row=>row.dataset.id)')
                    row=page.locator(f'.news[data-id="{item["id"]}"]');row.locator('.more-actions > summary').click();row.get_by_role('button',name='記事の詳細',exact=True).click()
                    page.locator('#article-note').fill('入力中のメモはそのまま');expect(page.locator('#article-note')).to_be_focused()
                    result={'title_ja':'更新された日本語の見出し','summary_ja':'新しい日本語の要点です。','comment':'ロボットの実装を試す際に使えます。','comment_version':1,'evidence_ids':[]}
                    store.save_analysis(item['id'],store.content_hash(item),'done',result)
                    server.enricher.revision+=1
                    page.evaluate('poll()')
                    expect(page.locator('#detail .detail-title')).to_have_text(result['title_ja'])
                    expect(row.locator('.news-title')).to_have_text(result['title_ja'])
                    expect(page.locator('#article-note')).to_have_value('入力中のメモはそのまま');expect(page.locator('#article-note')).to_be_focused()
                    assert page.locator('.news').evaluate_all('(rows)=>rows.map(row=>row.dataset.id)')==ids
                    expect(page.locator('#new-items')).not_to_be_visible()
                    page.locator('#detail-close').click()
                    # A failed refresh keeps the old display and retries on the next poll.
                    page.route('**/api/analyses?*',lambda route:route.fulfill(status=503,content_type='application/json',body='{"error":"fixture refresh failure"}'))
                    server.enricher.revision+=1;page.evaluate('poll()')
                    expect(row.locator('.news-title')).to_have_text(result['title_ja'])
                    page.unroute('**/api/analyses?*')
                    store.save_analysis(item['id'],store.content_hash(item),'error',error='日本語の解説が必要です: summary_ja')
                    # Previous results survive failures; a second item exposes an old saved error.
                    other=store.item(ids[-1]);store.save_analysis(other['id'],store.content_hash(other),'error',error='日本語の解説が必要です: summary_ja')
                    server.enricher.error='日本語の解説が必要です: summary_ja'
                    collector.update({'last_finished':3,'added':2})
                    page.evaluate('poll()')
                    expect(page.locator('body')).not_to_contain_text('summary_ja')
                    expect(page.locator('#notice')).to_contain_text('一部の記事の日本語解説を作れませんでした')
                    expect(page.locator('#new-items')).to_have_text('新着2件を表示')
                    expect(page.locator('.news')).to_have_count(120)
                    assert store.custom_filter(record['id'])==record
                    page.evaluate('scrollTo(0,0)');page.screenshot(path=str(out/f'after-{width}.png'))
                    assert not errors,errors
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    checks.append({'width':width,'aligned_buttons':True,'no_redundant_copy':True,'automatic_editorial_refresh':True,'preserved_rows':len(ids),'preserved_note_and_focus':True,'legacy_error_labels':True,'refresh_retry_and_new_items':True})
                finally:
                    context.close();server.shutdown();server.server_close();worker.join();store.close()
        browser.close()
    (out/'browser-results.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
