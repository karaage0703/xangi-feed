import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from xangi_feed.store import Store
from xangi_feed.article import ArticleImporter
from xangi_feed.discovery import Discovery


def candidate(url='https://new.example/robot', **extra):
    return {'id': 'feed-id-is-not-the-article-id', 'url': url, 'feed_url': 'https://new.example/rss',
            'title': 'ロボットの試作', 'summary': '音声対話の実装手順です。', 'published': 1794400000,
            'kind': 'blog', 'source_name': '工作ノート', 'reason': '関心のある実装記事です。',
            'matched_topics': ['ロボット'], 'personal_reason': '音声対話の試作とつながります。',
            'personal_sources': [{'id': 'R1', 'path': 'notes/robot.md', 'excerpt': '架空の試作記録'}], **extra}


class DiscoveryFeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'db'
        self.store = Store(self.path, seed=False)
        self.addCleanup(lambda: self.store.close())

    def test_normal_item_shared_across_all_discovery_and_read(self):
        self.assertEqual(self.store.ingest_discovery([candidate()]), 1)
        item = self.store.list_items(tab='discovery')['items'][0]
        self.assertEqual(self.store.list_items(tab='all')['items'][0]['id'], item['id'])
        self.assertEqual(self.store.item(item['id'])['discovery']['personal_sources'][0]['path'], 'notes/robot.md')
        self.assertEqual(item['summary'], candidate()['summary'])
        self.assertFalse(item['manually_added'])
        self.assertEqual(self.store.list_items(tab='manual')['total'], 0)
        self.assertEqual(self.store.list_items()['total'], 1)
        self.store.react(item['id'], {'read': True})
        self.assertEqual(self.store.list_items(tab='discovery')['total'], 0)
        self.assertEqual(self.store.list_items(tab='read')['items'][0]['id'], item['id'])
        self.assertEqual(self.store.list_items(tab='all')['total'], 1)
        self.store.react(item['id'], {'read': False})
        self.assertEqual(self.store.list_items(tab='discovery')['total'], 1)

    def test_rediscovery_preserves_every_user_field_and_canonical_identity(self):
        item = self.store.add_article({'url': 'https://new.example/robot', 'title': '本人が付けた見出し'})[0]
        self.store.react(item['id'], {'read': True, 'saved': True, 'rating': 1})
        self.store.save_note(item['id'], '自分のメモ')
        self.store.change_tag(item['id'], 'add', '試す')
        self.store.discussion(item['id'], 'sent', session_id='existing-session')
        before = self.store.item(item['id'])
        self.assertEqual(self.store.ingest_discovery([candidate(url='https://new.example/robot?utm_source=test')]), 0)
        self.store.ingest_discovery([candidate()])
        after = self.store.item(item['id'])
        for key in ['id', 'title', 'read', 'read_at', 'saved', 'rating', 'note', 'tags', 'session_id', 'content_origin', 'manually_added']:
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(self.store.list_items(tab='all')['total'], 1)
        self.assertEqual(self.store.list_items(tab='discovery')['total'], 0)
        self.assertEqual(self.store.list_items(tab='saved')['total'], 1)

    def test_rss_existing_article_reuses_id_without_registering_source(self):
        source = self.store.source(self.store.add_source('登録済み', 'https://known.example/rss', 'blog'))
        self.store.ingest(source, [{'url': candidate()['url'], 'title': '既存記事', 'summary': '既存本文', 'published': 1, 'topics': []}])
        item = self.store.list_items(tab='all')['items'][0]
        self.store.ingest_discovery([candidate()])
        found = self.store.list_items(tab='discovery')['items'][0]
        self.assertEqual(found['id'], item['id'])
        self.assertEqual(found['title'], '既存記事')
        self.assertEqual(len(self.store.sources()), 1)

    def test_history_filter_sort_and_mute_use_common_rules(self):
        self.store.ingest_discovery([candidate(), candidate(url='https://new.example/second', title='別の記事', published=1794500000)])
        newer = self.store.list_items(tab='discovery', limit=1)['items'][0]
        self.assertEqual(newer['title'], '別の記事')
        self.assertEqual(self.store.list_items(tab='discovery', q='ロボット')['total'], 1)
        self.assertEqual(self.store.list_items(tab='discovery', kinds=['paper'])['total'], 0)
        self.store.react(newer['id'], {'muted': True})
        self.assertEqual(self.store.list_items(tab='discovery')['total'], 1)
        self.assertEqual(self.store.list_items(tab='muted')['total'], 1)
        self.store.save_discovery({'items': [], 'updated': None, 'last_attempt': None})
        self.assertEqual(self.store.list_items(tab='discovery')['total'], 1)

    def test_upgrade_previous_metadata_and_restart_are_idempotent(self):
        self.store.save_discovery({'items': [candidate()], 'updated': 1, 'last_attempt': 1})
        self.store.close()
        self.store = Store(self.path, seed=False)
        item = self.store.list_items(tab='discovery')['items'][0]
        self.store.react(item['id'], {'read': True, 'saved': True})
        self.store.close()
        self.store = Store(self.path, seed=False)
        self.assertEqual(self.store.list_items(tab='all')['total'], 1)
        self.assertEqual(self.store.list_items(tab='read')['items'][0]['id'], item['id'])
        self.assertEqual(self.store.list_items(tab='saved')['total'], 1)

    def test_discovered_article_uses_same_explicit_body_fetch(self):
        self.store.ingest_discovery([candidate()])
        item = self.store.list_items(tab='discovery')['items'][0]
        importer = ArticleImporter(self.store, fetcher=lambda _: b'<html><article>Robot instructions</article></html>')
        updated = importer.fetch(item['id'])
        self.assertEqual(updated['content_text'], 'Robot instructions')
        self.assertFalse(updated['manually_added'])
        self.assertEqual(updated['discovery']['reason'], candidate()['reason'])

    def test_completed_search_imports_only_current_result(self):
        self.store.save_settings({'discovery_prompt': 'old', 'discovery_use_personal_context': False})
        host = Mock(configured=False)
        entered, release = threading.Event(), threading.Event()
        calls = []
        def search(*args, **kwargs):
            calls.append(kwargs['plan']['queries'][0])
            if len(calls) == 1:
                entered.set()
                release.wait(3)
                return [candidate(url='https://new.example/old')], {}
            return [candidate(url='https://new.example/latest')], {}
        service = Discovery(self.store, host, searcher=search)
        self.addCleanup(service.close)
        self.assertTrue(service.trigger(force=True))
        self.assertTrue(entered.wait(3))
        self.store.save_settings({'discovery_prompt': 'latest'})
        first = service.worker
        release.set(); first.join(3); service.worker.join(3)
        items = self.store.list_items(tab='all')['items']
        self.assertEqual([i['url'] for i in items], ['https://new.example/latest'])

    def test_invalid_legacy_candidate_does_not_break_startup(self):
        self.store.save_discovery({'items': [None, {'url': 'javascript:alert(1)'}, candidate()], 'updated': 1, 'last_attempt': 1})
        self.store.close(); self.store = Store(self.path, seed=False)
        self.assertEqual(self.store.list_items(tab='discovery')['total'], 1)
