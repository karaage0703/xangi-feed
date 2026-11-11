"""Score and publication identity checks against fixture data only."""
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / 'fixture.db', seed=False)
        fixtures = [
            ('blog', 'はてなブログ', 'https://mechanic.hatenablog.com/entry/1', 'ブログ記事A', '整備士の工作日記', '整備士'),
            ('blog', 'はてなブログ', 'https://another.hatenadiary.jp/entry/2', 'ブログ記事B', 'もう一人の長い名前のブログ・電子工作と音声対話の実験ノート', '別の著者'),
            ('podcast', 'Podcast', 'https://example.org/episode', '番組のエピソード', '工作ラジオ', '話し手'),
            ('video', 'YouTube', 'https://www.youtube.com/watch?v=abcdefghijk', '工作動画', '工作チャンネル', '工作チャンネル'),
            ('paper', 'arXiv', 'https://arxiv.org/abs/2611.00001', 'ロボット研究', 'ロボット論文', '研究者A / 研究者B'),
        ]
        ids = {}
        for i, (kind, source_name, url, title, publication, author) in enumerate(fixtures):
            feed_url = 'https://www.youtube.com/feeds/videos.xml?channel_id=UC' + 'A' * 22 if kind == 'video' else f'https://example.org/feed/{i}'
            source = store.source(store.add_source(source_name, feed_url, kind))
            store.ingest(source, [{'url': url, 'title': title, 'summary': '検証用の抜粋。', 'topics': ['ロボット'],
                                   'published': None, 'publication_name': publication, 'author': author}])
            ids[title] = next(item['id'] for item in store.list_items(tab='all')['items'] if item['title'] == title)
        manual = store.add_article({'url': 'https://note.com/mechanic/n/test', 'title': '配信元情報がない記事'})[0]
        server = Server(store, 'fixture-token')
        thread = threading.Thread(target=server.serve_forever); thread.start()
        checks = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                for width in [1280, 393, 360]:
                    for item_id in ids.values():
                        store.react(item_id, {'rating': 0})
                    page = browser.new_page(viewport={'width': width, 'height': 1000},
                                            extra_http_headers={'Authorization': 'Bearer fixture-token'})
                    errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(page.locator('.news')).to_have_count(6)
                    a = page.locator(f'.news[data-id="{ids["ブログ記事A"]}"]')
                    b = page.locator(f'.news[data-id="{ids["ブログ記事B"]}"]')
                    video = page.locator(f'.news[data-id="{ids["工作動画"]}"]')
                    expect(a.locator('.publisher')).to_contain_text('整備士の工作日記 / 著者: 整備士')
                    expect(a.locator('.publisher')).to_contain_text('mechanic.hatenablog.com')
                    expect(video.locator('.publisher')).to_have_text('工作チャンネル（www.youtube.com）')
                    expect(page.locator(f'.news[data-id="{manual["id"]}"] .publisher')).to_have_text('note.com/mechanic')
                    expect(a.locator('.preference-score')).to_have_text('好み 未評価')
                    a.get_by_role('button', name='ブログ記事Aを好きと評価', exact=True).click()
                    expect(a.locator('.preference-score')).to_have_text('好み +2')
                    expect(b.locator('.preference-score')).to_have_text('好み +1')
                    a.screenshot(path=str(output / f'{width}-good.png'))
                    a.locator('.more-actions > summary').click()
                    a.get_by_role('button', name='記事の詳細', exact=True).click()
                    expect(page.locator('#detail .preference-score')).to_have_text('好み +2')
                    expect(page.locator('#detail .publisher')).to_contain_text('整備士の工作日記')
                    page.locator('#detail').screenshot(path=str(output / f'{width}-detail.png'))
                    page.locator('#detail').get_by_role('button', name='ブログ記事Aを興味なしと評価', exact=True).click()
                    expect(page.locator('#detail .preference-score')).to_have_text('好み -2')
                    page.locator('#detail .reason select').select_option('duplicate')
                    expect(page.locator('#detail .preference-score')).to_have_text('好み 未評価')
                    page.locator('#detail-close').click()
                    expect(b.locator('.preference-score')).to_have_text('好み 未評価')
                    a.get_by_role('button', name='ブログ記事Aを好きと評価', exact=True).click()
                    b.get_by_role('button', name='ブログ記事Bを興味なしと評価', exact=True).click()
                    expect(video.locator('.preference-score')).to_have_text('好み 0')
                    page.reload()
                    expect(video.locator('.preference-score')).to_have_text('好み 0')
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert not errors, errors
                    checks.append({'width': width, 'identity_types': ['blog', 'podcast', 'video', 'paper', 'manual'],
                                   'score_states': ['unknown', 'positive', 'negative', 'zero'],
                                   'related_items_update_without_reordering': True, 'reload': True, 'page_errors': errors})
                    page.close()
                browser.close()
            (output / 'result.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2))
            print(json.dumps(checks, ensure_ascii=False))
        finally:
            server.shutdown(); server.server_close(); thread.join()
            server.collector.close(); server.enricher.close(); store.close()


if __name__ == '__main__':
    main()
