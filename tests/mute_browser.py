"""Article mute/unmute, failure handling and preserved data in fixture UI."""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, os.environ.get('FEED_TEST_REPO', str(Path(__file__).resolve().parent.parent)))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / 'fixture.db', seed=False)
        ids = [store.add_article({'url': f'https://example.org/{i}', 'title': f'工作の記事 {i}'})[0]['id'] for i in range(3)]
        a = ids[0]
        store.react(a, {'saved': True, 'rating': 1})
        store.save_note(a, '残しておくメモ')
        store.change_tag(a, 'add', '工作')
        server = Server(store, 'fixture-token-' * 4)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        try:
            checks = []
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                for label, width in [('desktop', 1280), ('mobile', 393), ('small-mobile', 360)]:
                    for item in ids:
                        store.react(item, {'muted': False, 'read': False})
                    before = store.item(a); profile = store.profile()
                    context = browser.new_context(viewport={'width': width, 'height': 900}, has_touch=width < 900, extra_http_headers={'Authorization': 'Bearer ' + 'fixture-token-' * 4})
                    page = context.new_page(); errors = []; dialogs = []; opened = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('dialog', lambda dialog: (dialogs.append(dialog.type), dialog.dismiss()))
                    page.on('request', lambda request: opened.append(request.url) if request.url.endswith('/open') else None)
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(page.locator('.news')).to_have_count(3)
                    row = page.locator(f'article[data-id="{a}"]')
                    control = row.locator('.mute-control')
                    expect(control).to_be_visible(); expect(control).to_have_text('ミュート')
                    assert control.evaluate('el=>!el.closest("details") && el.getBoundingClientRect().height>=44')
                    toolbar = row.locator('.compact-actions')
                    assert control.evaluate('el=>!!el.closest(".compact-actions")')
                    boxes = [locator.bounding_box() for locator in [control, row.locator('.rating-control').first, row.locator('.notion-action'), row.locator('.more-actions>summary')]]
                    assert max(box['y'] for box in boxes) - min(box['y'] for box in boxes) < 2, boxes
                    assert toolbar.evaluate('el=>el.scrollWidth<=el.clientWidth')
                    page.screenshot(path=str(output / f'{label}-skip.png'), full_page=True)

                    # Failure leaves the row visible and the persisted state untouched.
                    endpoint = f'**/api/items/{a}/react'
                    page.route(endpoint, lambda route: route.fulfill(status=503, json={'error': 'Fixture save failed'}))
                    control.tap() if width < 900 else control.click()
                    expect(page.locator('#notice')).to_contain_text('Fixture save failed')
                    expect(row).to_be_visible(); expect(control).to_be_enabled()
                    assert not store.item(a)['muted']
                    page.unroute(endpoint)

                    # Two immediate click events still produce exactly one write.
                    posts = []
                    def delayed(route):
                        posts.append(route.request.post_data_json); time.sleep(.1); route.continue_()
                    page.route(endpoint, delayed)
                    control.evaluate('el=>{el.click();el.click();}')
                    expect(row).to_have_count(0)
                    expect(page.locator('.news')).to_have_count(2)
                    assert posts == [{'muted': True}]
                    assert page.locator('.mute-control:focus').count() == 1
                    page.unroute(endpoint)
                    after = store.item(a)
                    for key in ['read', 'read_at', 'saved', 'rating', 'reason', 'note', 'note_updated', 'tags']:
                        assert after[key] == before[key], key
                    assert store.profile() == profile
                    page.reload(); expect(page.locator('.news')).to_have_count(2)
                    page.locator('[data-tab=read]').click(); expect(page.locator('.news')).to_have_count(0)
                    page.locator('[data-tab=all]').click(); expect(page.locator('.news')).to_have_count(2)
                    page.locator('[data-tab=muted]').click(); expect(page.locator('.news')).to_have_count(1)
                    row = page.locator(f'article[data-id="{a}"]')
                    expect(row.locator('.mute-control')).to_have_text('ミュートを解除')
                    page.screenshot(path=str(output / f'{label}-restore.png'), full_page=True)
                    restore = page.locator('.news .mute-control')
                    restore.tap() if width < 900 else restore.click()
                    expect(page.locator('.news')).to_have_count(0)
                    assert not store.item(a)['read']; assert store.item(a)['saved']
                    page.locator('[data-tab=recommended]').click(); expect(page.locator('.news')).to_have_count(3)

                    # Detail controls are also one click and retain unsaved note drafts.
                    row = page.locator(f'article[data-id="{a}"]')
                    row.locator('.more-actions summary').click()
                    row.get_by_role('button', name='記事の詳細', exact=True).click()
                    draft = page.locator('#detail .note-editor textarea')
                    draft.fill('保存前の下書きも保持')
                    page.locator('#detail .mute-control').click()
                    expect(page.locator('#detail .mute-control')).to_have_text('ミュートを解除')
                    expect(draft).to_have_value('保存前の下書きも保持')
                    expect(page.locator('.news')).to_have_count(2)
                    page.locator('#detail .mute-control').click()
                    expect(page.locator('#detail .mute-control')).to_have_text('ミュート')
                    expect(draft).to_have_value('保存前の下書きも保持')
                    assert store.item(a)['note'] == before['note']
                    page.locator('#detail-close').click()
                    expect(page.locator('.news')).to_have_count(3)

                    # Muting the last row focuses the mute category; all articles remain restorable there.
                    for remaining in [2, 1, 0]:
                        button = page.locator('.news .mute-control').first
                        button.tap() if width < 900 else button.click()
                        expect(page.locator('.news')).to_have_count(remaining)
                    expect(page.locator('[data-tab=muted]')).to_be_focused()
                    page.locator('[data-tab=muted]').click(); expect(page.locator('.news')).to_have_count(3)
                    expect(page.locator('.news .mute-control')).to_have_count(3)
                    # Restore all, then mute the same article from each normal category.
                    for _ in ids:
                        page.locator('.news .mute-control').first.click()
                    expect(page.locator('.news')).to_have_count(0)
                    for tab in ['all', 'read', 'saved', 'notes', 'manual', 'discussed']:
                        store.react(a, {'read': True})
                        store.discussion(a, 'sent', 'fixture-conversation')
                        page.locator(f'[data-tab={tab}]').click()
                        row = page.locator(f'article[data-id="{a}"]')
                        expect(row).to_be_visible()
                        expect(row.locator('.mute-control')).to_be_visible()
                        row.locator('.mute-control').click()
                        expect(row).to_have_count(0)
                        page.locator('[data-tab=muted]').click()
                        expect(page.locator('.news')).to_have_count(1)
                        expect(page.locator('.news .mute-control')).to_have_attribute('aria-pressed', 'true')
                        page.locator('.news .mute-control').click()
                        expect(page.locator('.news')).to_have_count(0)
                        page.locator(f'[data-tab={tab}]').click()
                        expect(page.locator(f'article[data-id="{a}"]')).to_be_visible()
                    # Navigating before a mute response must not decrement another list.
                    page.locator('[data-tab=all]').click()
                    pending = []
                    page.route(endpoint, lambda route: pending.append(route))
                    page.locator(f'article[data-id="{a}"] .mute-control').click()
                    page.locator('[data-tab=recommended]').click()
                    expect(page.locator('.news')).to_have_count(2)
                    assert len(pending) == 1
                    pending[0].continue_()
                    expect(page.locator('#notice')).to_contain_text('ミュートしました')
                    expect(page.locator('#result-count')).to_have_text('2件')
                    page.unroute(endpoint)
                    page.locator('[data-tab=muted]').click()
                    expect(page.locator('.news')).to_have_count(1)
                    page.locator('.news .mute-control').click()
                    page.locator('[data-tab=muted]').click()
                    expect(page.locator('#feed')).to_contain_text('ミュートした記事が見つかりません')
                    page.screenshot(path=str(output / f'{label}-mute-empty.png'))
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert not dialogs, dialogs
                    assert not opened, opened
                    assert not errors, errors
                    assert store.profile() == profile
                    checks.append({'viewport': label, 'single_button_mute_restore': True, 'failure_keeps_row': True, 'double_click_one_write': True, 'unread_and_user_data_preserved': True, 'detail_draft_preserved': True, 'last_row_focus': True, 'all_views_mute_and_restore': True, 'no_confirmation_or_article_open': True, 'console_errors': errors})
                    context.close()
                browser.close()
            (output / 'result.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2))
            print(json.dumps({'result': 'passed', 'checks': checks}, ensure_ascii=False))
        finally:
            server.shutdown(); server.server_close(); thread.join()
            server.collector.close(); server.enricher.close(); store.close()


if __name__ == '__main__':
    main()
