"""Shared recommendation/article workflow with isolated data and no network/model."""
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.store import Store
from xangi_feed.server import Server
from xangi_feed.discovery import Discovery


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='/snap/bin/chromium', args=['--no-sandbox'])
        for width in [1280, 390, 360]:
            with tempfile.TemporaryDirectory() as tmp:
                store = Store(Path(tmp) / 'db', seed=False)
                store.save_settings({'enabled': False, 'discovery_enabled': False, 'discovery_use_personal_context': False,
                                     'discovery_prompt': 'ロボット', 'mark_read_on_open': False, 'show_link_previews': False})
                existing = store.add_article({'url': 'https://new.example/robot', 'title': 'ロボットの試作手順'})[0]
                store.add_article({'url': 'https://other.example/post', 'title': '別の記事'})
                row = {'id': 'candidate', 'url': existing['url'], 'feed_url': 'https://new.example/rss',
                       'title': existing['title'], 'published': 1794460000, 'reason': '手元の試作に使える記事です。',
                       'source_name': '架空の工作ノート', 'kind': 'blog', 'matched_topics': ['ロボット']}
                server = Server(store, 'fixture', host=Mock(configured=False))
                server.discovery = Discovery(store, server.host, searcher=lambda *a, **kw: ([row], {}))
                server.discovery.trigger(force=True); server.discovery.worker.join(3)
                thread = threading.Thread(target=server.serve_forever); thread.start()
                context = browser.new_context(viewport={'width': width, 'height': 900}, extra_http_headers={'Authorization': 'Bearer fixture'})
                page = context.new_page(); errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                context.route('**/*', lambda r: r.continue_() if r.request.url.startswith('http://127.0.0.1:') else r.abort())
                try:
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    page.get_by_role('button', name='おすすめ', exact=True).click()
                    card = page.locator('#feed .news')
                    expect(card).to_have_count(1)
                    expect(card).to_have_attribute('data-id', existing['id'])
                    expect(card.locator('.ai-comment')).to_be_visible()
                    expect(card.locator('.rating-control')).to_have_count(2)
                    expect(card.locator('.note-control')).to_be_visible()
                    expect(card.locator('.notion-action')).to_have_count(1)
                    expect(card).to_contain_text(row['reason'])
                    card.locator('.rating-control').first.click()
                    expect(card.locator('.rating-control').first).to_have_attribute('aria-pressed', 'true')
                    card.locator('.bookmark-control').click()
                    expect(card.locator('.bookmark-control')).to_have_attribute('aria-pressed', 'true')
                    card.locator('.note-control').click()
                    page.locator('#article-note').fill('実装時に試すメモ')
                    page.get_by_role('button', name='メモを保存', exact=True).click()
                    expect(page.locator('.note-message')).to_contain_text('メモを保存しました')
                    page.locator('#manual-tag-input').fill('試す')
                    page.get_by_role('button', name='タグを追加', exact=True).click()
                    expect(page.locator('#detail .manual-tag-assigned')).to_contain_text('試す')
                    page.locator('#detail-close').click()
                    expect(card).to_contain_text('実装時に試すメモ')
                    page.evaluate('window.scrollTo(0,0)')
                    page.screenshot(path=str(output / f'common-{width}.png'), full_page=True)
                    page.get_by_role('button', name='すべて', exact=True).click()
                    expect(page.locator('#feed .news')).to_have_count(2)
                    shared = page.locator(f'#feed .news[data-id="{existing["id"]}"]')
                    expect(shared.locator('.bookmark-control')).to_have_attribute('aria-pressed', 'true')
                    expect(shared).to_contain_text('実装時に試すメモ')
                    page.get_by_role('button', name='おすすめ', exact=True).click()
                    expect(card).to_have_count(1)
                    card.locator('.more-actions > summary').click()
                    card.locator('.read-control').click()
                    expect(card).to_have_count(0)
                    page.get_by_role('button', name='読了', exact=True).click()
                    expect(card).to_have_attribute('data-id', existing['id'])
                    expect(card.locator('.bookmark-control')).to_have_attribute('aria-pressed', 'true')
                    card.locator('.more-actions > summary').click()
                    card.locator('.read-control').click()
                    expect(card).to_have_count(0)
                    page.get_by_role('button', name='おすすめ', exact=True).click()
                    expect(card).to_have_count(1)
                    card.locator('.mute-control').click()
                    expect(card).to_have_count(0)
                    page.get_by_role('button', name='ミュート', exact=True).click()
                    expect(card).to_have_count(1)
                    card.locator('.mute-control').click()
                    page.get_by_role('button', name='おすすめ', exact=True).click()
                    expect(card).to_have_count(1)
                    # Repeated searches share the same article and user state.
                    server.discovery.trigger(force=True); server.discovery.worker.join(3)
                    page.reload(); page.get_by_role('button', name='おすすめ', exact=True).click()
                    expect(card).to_have_count(1)
                    expect(card.locator('.bookmark-control')).to_have_attribute('aria-pressed', 'true')
                    # Default link-open read behavior uses the shared endpoint as well.
                    store.save_settings({'mark_read_on_open': True})
                    context.route(existing['url'], lambda r: r.fulfill(content_type='text/html', body='<h1>原文</h1>'))
                    with context.expect_page() as opened:
                        card.locator('.news-title').click()
                    opened.value.wait_for_load_state(); opened.value.close()
                    expect(card).to_have_count(0)
                    page.get_by_role('button', name='読了', exact=True).click()
                    expect(card).to_have_count(1)
                    page.evaluate('window.scrollTo(0,0)')
                    page.screenshot(path=str(output / f'read-{width}.png'), full_page=True)
                    assert store.list_items(tab='all')['total'] == 2
                    assert not errors, errors
                    checks.append({'width': width, 'same_id': True, 'common_actions': True, 'all': True,
                                   'bookmark_note_tag': True, 'read_unread': True, 'mute': True,
                                   'rediscovery': True, 'link_open_read': True, 'errors': errors})
                finally:
                    context.close(); server.shutdown(); server.server_close(); thread.join(); store.close()
        browser.close()
    (output / 'checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == '__main__':
    main()
