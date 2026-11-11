"""Real browser paths with fixture AI/feed only; no external requests or user data."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, os.environ.get('FEED_TEST_REPO', str(Path(__file__).resolve().parent.parent)))
from playwright.sync_api import expect, sync_playwright
from xangi_feed.server import Server
from xangi_feed.store import Store
from xangi_feed.discovery import Discovery


def main():
    output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='/snap/bin/chromium', args=['--no-sandbox'])
        for width in [1280, 390]:
            with tempfile.TemporaryDirectory() as tmp:
                store = Store(Path(tmp) / 'fixture.db', seed=False)
                one, _ = store.add_article({'url': 'https://known.example/one', 'title': 'Robot maker fixture'})
                two, _ = store.add_article({'url': 'https://known.example/two', 'title': 'Robot fixture second'})
                store.add_article({'url': 'https://unrelated.example/plain', 'title': 'Weather fixture'})
                store.react(one['id'], {'rating': 1})
                store.save_settings({'agent_id': 'fixture', 'enabled': False, 'discovery_enabled': True})
                import time
                from email.utils import formatdate
                published = formatdate(time.time(), usegmt=True)
                feed = f'<rss><channel><title>新しいロボット研究所</title><item><title>実験手順を公開したロボット</title><link>https://new.example/robot</link><pubDate>{published}</pubDate></item></channel></rss>'.encode()
                row = {'feed_url': 'https://new.example/rss', 'url': 'https://new.example/robot', 'reason': 'ロボットの試作手順が参考になります。', 'matched_topics': ['ロボット'], 'kind': 'blog'}
                host = Mock(configured=True); host.run.return_value = json.dumps([row]); host.agents.return_value = []
                server = Server(store, 'fixture-token-' * 4, host=host)
                server.discovery = Discovery(store, host, fetcher=lambda _: feed, searcher=lambda *args, **kwargs: ([{**row, 'id': 'fixture-candidate', 'title': '実験手順を公開したロボット', 'source_name': '新しいロボット研究所', 'published': time.time(), 'verified_at': time.time(), 'language': 'ja'}], {'feeds_checked': 1}))
                # Run the scheduler, not just its manual entry, against a fake agent.
                server.discovery.start()
                server.discovery.worker.join(3) if server.discovery.worker else None
                thread = threading.Thread(target=server.serve_forever); thread.start()
                context = browser.new_context(viewport={'width': width, 'height': 900}, extra_http_headers={'Authorization': 'Bearer ' + 'fixture-token-' * 4})
                page = context.new_page(); errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                try:
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    threshold = page.locator('[name=recommendation_min_preference]')
                    expect(threshold).not_to_be_visible()
                    page.locator('#recommendation-toggle > summary').click()
                    expect(threshold).to_be_visible()
                    expect(page.locator('#feed .news')).to_have_count(3)
                    threshold.fill('2'); threshold.press('Tab')
                    expect(page.locator('#recommendation-message')).to_have_text('保存して反映しました。')
                    # The liked article has robot + maker weights and its own rating, the second only robot.
                    expect(page.locator('#feed .news')).to_have_count(1)
                    page.locator('#sort').select_option('published')
                    expect(page.locator('#feed .news')).to_have_count(1)
                    page.reload(); expect(threshold).not_to_be_visible()
                    expect(threshold).to_have_value('2')
                    page.locator('#recommendation-toggle > summary').click()
                    expect(page.locator('#feed .news')).to_have_count(1)
                    page.screenshot(path=str(output / f'custom-{width}.png'), full_page=True)
                    threshold.fill(''); threshold.press('Tab')
                    expect(page.locator('#feed .news')).to_have_count(3)
                    page.get_by_role('button', name='おすすめ', exact=True).click()
                    expect(page.locator('#discovery-panel')).to_be_visible()
                    expect(page.locator('.discovery-card')).to_have_count(1)
                    expect(page.locator('.discovery-card')).to_contain_text('実験手順')
                    expect(page.locator('#recommendation-panel')).not_to_be_visible()
                    expect(page.locator('#feed')).not_to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(output / f'discovery-{width}.png'), full_page=True)
                    page.locator('[name=discovery_enabled]').uncheck()
                    page.locator('[name=discovery_interval_hours]').fill('48')
                    expect(page.locator('#discovery-save-message')).to_have_text('自動保存して反映しました。')
                    deadline=time.monotonic()+5
                    while store.settings()['discovery_interval_hours'] != 48 and time.monotonic()<deadline: page.wait_for_timeout(50)
                    assert store.settings()['discovery_enabled'] is False
                    assert store.settings()['discovery_interval_hours'] == 48
                    expect(page.get_by_role('button', name='配信元に登録', exact=True)).to_have_count(0)
                    title = page.locator('.discovery-card h3 a')
                    expect(title).to_have_attribute('href', row['url'])
                    context.route(row['url'], lambda route: route.fulfill(content_type='text/html; charset=utf-8', body='<h1>記事の本文</h1>'))
                    with context.expect_page() as opened:
                        title.click()
                    opened.value.wait_for_load_state()
                    expect(opened.value.locator('h1')).to_have_text('記事の本文')
                    opened.value.close()
                    assert store.sources() == []
                    form = page.locator('#discovery-form')
                    form.locator('[name=discovery_prompt]').fill('日本語の記事だけ探してください。')
                    expect(page.locator('#discovery-save-message')).to_have_text('自動保存して反映しました。')
                    expect(page.locator('#discovery-message')).to_contain_text('日本語のみ')
                    expect(page.locator('.discovery-card')).to_have_count(1)
                    page.reload()
                    page.get_by_role('button', name='おすすめ', exact=True).click()
                    expect(form.locator('[name=discovery_prompt]')).to_have_value('日本語の記事だけ探してください。')
                    page.get_by_role('button', name='すべて', exact=True).click()
                    expect(page.locator('#feed')).to_be_visible()
                    expect(page.locator('#feed .news')).to_have_count(3)
                    page.get_by_role('button', name='カスタム', exact=True).click()
                    expect(threshold).not_to_be_visible()
                    page.locator('#recommendation-toggle > summary').click()
                    expect(threshold).to_be_visible()
                    assert errors == [], errors
                    print(f'PASS {width}: score filter, sort independence, clear/reload, periodic fixture discovery, article title navigation, free prompt persistence, schedule settings, return to lists, no runtime errors')
                finally:
                    context.close(); server.shutdown(); server.server_close(); thread.join(); store.close()
        browser.close()


if __name__ == '__main__':
    main()
