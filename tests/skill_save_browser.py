"""Offline saving flow: local user policy, AI request, result callback and reload."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store

PAGE = '11111111-1111-4111-8111-111111111111'


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
        for width in [1280, 390, 360]:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                policy = root / 'skills/my-save/SKILL.md'
                policy.parent.mkdir(parents=True); policy.write_text('個別の保存先と方針')
                store = Store(root / '.xangi-feed/feed.db', seed=False)
                store.save_settings({'enabled': False})
                item, _ = store.add_article({'url': 'https://example.org/article', 'title': '保存の確認記事'})
                host = Mock(configured=True, agent_id='')
                host.create.return_value = 'saving-session'
                host.agents.return_value = [{'id': 'saving-ai', 'name': '保存担当', 'available': True}]
                server = Server(store, 'fixture', host=host)
                worker = threading.Thread(target=server.serve_forever); worker.start()
                context = browser.new_context(viewport={'width': width, 'height': 900}, extra_http_headers={'Authorization': 'Bearer fixture'})
                page = context.new_page(); errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                origin = f'http://127.0.0.1:{server.server_port}'
                try:
                    page.goto(origin + '/')
                    page.locator('#settings-open').click()
                    expect(page.locator('#notion-form')).to_have_count(0)
                    page.locator('#save-form [name=save_skill_name]').fill('my-save')
                    page.locator('#save-form [name=save_agent_id]').select_option('saving-ai')
                    page.locator('#save-form button[type=submit]').click()
                    expect(page.locator('#save-message')).to_contain_text('設定しました')
                    page.screenshot(path=str(output / f'settings-{width}.png'))
                    page.locator('#settings-close').click()
                    row = page.locator(f'.news[data-id="{item["id"]}"]')
                    row.get_by_role('button', name='メモを書く', exact=True).click()
                    page.locator('#article-note').fill('保存担当へ渡す下書き')
                    page.locator('#detail-close').click()
                    row.locator('[data-action=notion]').evaluate('(b) => { b.click(); b.click(); }')
                    expect(row.locator('.notion-status')).to_contain_text('保存AIへ依頼済み')
                    expect(row.locator('.notion-status')).not_to_contain_text('保存完了')
                    expect(row.get_by_role('link', name='保存用の会話を開く')).to_have_attribute('href', origin + '/chat/saving-session')
                    assert store.item(item['id'])['note'] == '保存担当へ渡す下書き'
                    host.send.assert_called_once()
                    host.create.assert_called_once_with(agent_id='saving-ai')
                    assert str(policy) in host.send.call_args.args[1]
                    assert '保存担当へ渡す下書き' in host.send.call_args.args[1]
                    page.reload(); row.locator('[data-action=notion]').click()
                    expect(row.locator('.notion-status')).to_contain_text('保存AIへ依頼済み')
                    host.send.assert_called_once()
                    pending = store.save_request(item['id'])
                    report = {'request_id': pending['request_id'], 'session_id': pending['session_id'], 'status': 'needs_input', 'summary': '保存先を教えてください'}
                    response = context.request.post(origin + f'/api/items/{item["id"]}/save-result', data=report)
                    assert response.status == 200, response.text()
                    page.reload()
                    expect(row.locator('.notion-status')).to_contain_text('保存AIから確認があります')
                    report.update(status='saved', page_url='https://www.notion.so/' + PAGE, summary='要約とメモを保存して照合しました')
                    response = context.request.post(origin + f'/api/items/{item["id"]}/save-result', data=report)
                    assert response.status == 200, response.text()
                    # Completion is observed via the normal health polling flow.
                    expect(row.locator('.notion-status')).to_contain_text('AIから保存完了の報告', timeout=15000)
                    expect(row.get_by_role('link', name='Notionで開く')).to_have_attribute('href', 'https://www.notion.so/' + PAGE.replace('-', ''))
                    page.reload(); expect(row.locator('.notion-status')).to_contain_text('AIから保存完了の報告')
                    page.locator('#settings-open').click()
                    expect(page.locator('#save-form [name=save_skill_name]')).to_have_value('my-save')
                    page.locator('#settings-close').click()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'page overflow'
                    assert not errors, errors
                    page.screenshot(path=str(output / f'completed-{width}.png'))
                    results.append({'width': width, 'custom_policy': True, 'draft_saved': True, 'duplicate_prevented': True, 'not_false_completion': True, 'needs_input': True, 'reported_completion_polled': True, 'reload': True, 'page_errors': errors})
                finally:
                    context.close(); server.shutdown(); server.server_close(); worker.join(); store.close()
        browser.close()
    (output / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    main()
