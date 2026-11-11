"""Manual tag lifecycle against fixture data only."""
import json
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.store import Store
from xangi_feed.server import Server


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / 'fixture.db', seed=False)
        ids = [store.add_article({'url': 'https://example.org/' + str(i), 'title': '検証用の記事 ' + str(i)})[0]['id'] for i in range(2)]
        for item in ids: store.save_note(item, '削除しないメモ')
        server = Server(store, 'fixture-token-' * 4)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        try:
            checks = []
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                for label, width in [('desktop', 1280), ('mobile', 393), ('small-mobile', 360)]:
                    name = '工作で試す ' + label
                    page = browser.new_page(viewport={'width': width, 'height': 900}, extra_http_headers={'Authorization': 'Bearer ' + 'fixture-token-' * 4})
                    errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                    accept = [False]; page.on('dialog', lambda dialog: dialog.accept() if accept[0] else dialog.dismiss())
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    expect(page.locator('.news')).to_have_count(2)
                    def open_tags(item):
                        row = page.locator(f'.news[data-id="{item}"]')
                        row.locator('.more-actions summary').click()
                        row.get_by_role('button', name='タグを編集', exact=True).click()
                        expect(page.locator('#manual-tag-input')).to_be_focused()
                    def add_tag():
                        page.locator('#manual-tag-input').fill(name)
                        page.get_by_role('button', name='タグを追加', exact=True).click()
                        expect(page.get_by_role('button', name=f'この記事からタグ「{name}」を外す', exact=True)).to_be_visible()
                    open_tags(ids[0]); add_tag()
                    page.locator('.manual-tag-editor').screenshot(path=str(output / f'{label}-editor.png'))
                    page.locator('#detail-close').click()
                    open_tags(ids[1]); add_tag()
                    page.locator('#detail-close').click()
                    page.reload()
                    expect(page.get_by_role('button', name=f'自分のタグ「{name}」で絞り込む', exact=True)).to_have_count(2)
                    open_tags(ids[0])
                    page.get_by_role('button', name=f'この記事からタグ「{name}」を外す', exact=True).click()
                    expect(page.get_by_role('button', name=f'この記事からタグ「{name}」を外す', exact=True)).to_have_count(0)
                    page.locator('#detail-close').click()
                    page.get_by_role('button', name=f'自分のタグ「{name}」で絞り込む', exact=True).click()
                    expect(page.locator('.news')).to_have_count(1)
                    if width <= 900: page.locator('#classification-open').click()
                    page.locator('#tag-management summary').click()
                    remove = page.get_by_role('button', name=f'タグ「{name}」をすべての記事から削除', exact=True)
                    remove.click()
                    expect(remove).to_have_count(1)
                    page.locator('#tag-management').screenshot(path=str(output / f'{label}-management.png'))
                    accept[0] = True; remove.click()
                    expect(remove).to_have_count(0)
                    expect(page.locator('#tag-management-message')).to_contain_text('記事は残っています')
                    if width <= 900: page.locator('#classification-close').click()
                    expect(page.locator('.news')).to_have_count(2)
                    assert all(store.item(item)['tags'] == [] and store.item(item)['note'] == '削除しないメモ' for item in ids)
                    assert store.list_items(tab='all')['total'] == 2
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert not errors, errors
                    checks.append({'viewport': label, 'add_reuse_reload_detach_filter_cancel_delete': True, 'articles_and_notes_preserved': True, 'console_errors': errors})
                    page.close()
                browser.close()
            (output / 'result.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2))
            print(json.dumps({'result': 'passed', 'checks': checks}, ensure_ascii=False))
        finally:
            server.shutdown(); server.server_close(); thread.join(); server.collector.close(); server.enricher.close(); store.close()


if __name__ == '__main__': main()
