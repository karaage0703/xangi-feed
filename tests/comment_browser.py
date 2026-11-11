"""Grounded comment bubbles and fallback states, with fixture data only."""
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
        ids, snapshots = {}, {}
        for name in ['grounded', 'empty', 'failed-search', 'pending', 'failed-refresh', 'unused-records']:
            item = store.add_article({'url': 'https://example.org/' + name, 'title': '音声対話ロボット ' + name})[0]
            ids[name] = item['id']
            fields = {'title_ja': '音声対話ロボットの作り方', 'summary_ja': 'マイクとスピーカーをつないで、手元のロボットと会話する実装例です。'}
            if name != 'pending':
                source = {'id': 'S1', 'path': 'projects/voice-robot.md', 'excerpt': '音声対話の試作で、認識から返答までの遅延を改善したい。'}
                context_status = 'empty' if name == 'empty' else 'error' if name == 'failed-search' else 'ready'
                fields.update(comment='試作中の音声対話ロボットで、返答までの遅延を測る手順の参考になります。まずマイクとモデルの条件をそろえて比較できそうです。' if context_status == 'ready' else '音声対話の実装手順を確認できます。マイクとモデルの条件で遅延が変わるため、測定条件をそろえて試すとよさそうです。',
                              comment_version=1, evidence_ids=['S1'] if name in {'grounded', 'failed-refresh'} else [],
                              comment_context={'status': context_status, 'sources': [source] if name in {'grounded', 'failed-refresh'} else [], 'degraded': False})
            store.save_analysis(item['id'], store.content_hash(item), 'done', fields)
            snapshots[name] = fields
            if name == 'pending':
                store.save_analysis(item['id'], store.content_hash(item), 'pending')
            if name == 'failed-refresh':
                store.save_analysis(item['id'], store.content_hash(item), 'error', error='検証用の生成失敗')
        class FixtureHost:
            configured = True
            agent_id = ''

            def run(self, *_args, **_kwargs):
                raise AssertionError('LLM must not run in browser fixtures')

        server = Server(store, 'fixture-token', host=FixtureHost()); thread = threading.Thread(target=server.serve_forever); thread.start()
        checks = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                for scheme in ['light', 'dark']:
                    for width in [1280, 393, 360]:
                        cached = store.item(ids['grounded'])
                        store.save_analysis(cached['id'], store.content_hash(cached), 'done', snapshots['grounded'])
                        page = browser.new_page(viewport={'width': width, 'height': 1000}, color_scheme=scheme,
                                                extra_http_headers={'Authorization': 'Bearer fixture-token'})
                        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(f'http://127.0.0.1:{server.server_port}')
                        expect(page.locator('.news')).to_have_count(6)
                        def row(name):
                            return page.locator(f'.news[data-id="{ids[name]}"]')
                        grounded = row('grounded')
                        expect(grounded.get_by_role('note', name='AIコメント')).to_be_visible()
                        expect(grounded.locator('.ai-comment-text')).to_contain_text('試作中の音声対話ロボット')
                        expect(row('empty').locator('.ai-comment-basis')).to_contain_text('関連する記録なし')
                        expect(row('failed-search').locator('.ai-comment-basis')).to_contain_text('取得できず')
                        expect(row('pending').locator('.ai-comment-basis')).to_contain_text('準備中')
                        expect(row('pending').locator('.editorial > .excerpt')).to_contain_text('マイクとスピーカー')
                        expect(row('failed-refresh').locator('.ai-comment .source-error')).to_contain_text('前回のコメント')
                        expect(row('unused-records').locator('.ai-comment-basis')).to_contain_text('未参照')
                        records = grounded.locator('.ai-comment-records')
                        records.locator('summary').focus(); records.locator('summary').press('Enter')
                        expect(records.get_by_text('S1: projects/voice-robot.md', exact=True)).to_be_visible()
                        grounded.screenshot(path=str(output / f'{scheme}-{width}-bubble.png'))
                        grounded.locator('.more-actions > summary').click()
                        grounded.get_by_role('button', name='記事の詳細', exact=True).click()
                        expect(page.locator('#detail .ai-comment-text')).to_contain_text('試作中の音声対話ロボット')
                        page.locator('#detail .ai-comment-records > summary').click()
                        expect(page.locator('#detail .ai-comment-records')).to_contain_text('返答までの遅延')
                        page.locator('#detail').screenshot(path=str(output / f'{scheme}-{width}-detail.png'))
                        page.locator('#detail-close').click()
                        page.reload()
                        expect(grounded.locator('.ai-comment-text')).to_be_visible()
                        store.save_settings({'agent_id': 'fixture-agent'})
                        grounded.get_by_role('button', name='AIコメントを作り直す', exact=True).click()
                        expect(grounded.get_by_role('button', name='AIコメントを準備中', exact=True)).to_be_disabled()
                        expect(grounded.locator('.ai-comment-basis').last).to_contain_text('前回の内容')
                        expect(grounded.locator('.editorial > .excerpt')).to_contain_text('マイクとスピーカー')
                        store.save_settings({'agent_id': ''})
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        assert not errors, errors
                        checks.append({'scheme': scheme, 'width': width, 'grounded_and_fallback_states': True,
                                       'existing_summary_preserved': True, 'keyboard_sources_detail_reload': True,
                                       'regenerate_preserves_previous_content': True,
                                       'page_errors': errors, 'no_horizontal_overflow': True})
                        page.close()
                browser.close()
            (output / 'result.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2))
            print(json.dumps(checks, ensure_ascii=False))
        finally:
            server.shutdown(); server.server_close(); thread.join()
            server.collector.close(); server.enricher.close(); store.close()


if __name__ == '__main__':
    main()
