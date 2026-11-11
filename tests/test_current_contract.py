import tempfile
import unittest
from pathlib import Path
from xangi_feed.store import Store


class CurrentContractTests(unittest.TestCase):
    def test_removed_settings_and_api_aliases_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'feed.db', seed=False)
            try:
                item, _ = store.add_article({'url': 'https://example.org/article'})
                for key in ['recommendation_min_importance', 'recommendation_include_unanalysed', 'preferences', 'notion_parent_id', 'save_blog_pdf', 'save_notion', 'recommendation_llm_enabled', 'recommendation_llm_prompt', 'recommendation_llm_min_score']:
                    self.assertNotIn(key, store.settings())
                    with self.subTest(key=key), self.assertRaises(ValueError): store.save_settings({key: True})
                with self.assertRaises(ValueError): store.save_settings({'recommendation_priority':'importance'})
                with self.assertRaises(ValueError): store.react(item['id'], {'skipped':True})
                with self.assertRaises(ValueError): store.react(item['id'], {'muted':True, 'skipped':True})
                current = store.react(item['id'], {'muted':True})
                self.assertTrue(current['muted'])
                self.assertNotIn('skipped',current)
                self.assertNotIn('notion',current)
                self.assertEqual(store.list_items(tab='muted')['total'],1)
            finally: store.close()

    def test_current_discussion_and_analysis_routes_remain_available(self):
        import json
        import threading
        from unittest.mock import Mock
        from urllib.request import Request, urlopen
        from xangi_feed.server import Server
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'feed.db', seed=False)
            item,_ = store.add_article({'url':'https://example.org/article'})
            server = Server(store,'fixture')
            server.enricher.enqueue = Mock(return_value=1)
            server.discussions.open = Mock(return_value={'session_id':'fixture-session'})
            worker = threading.Thread(target=server.serve_forever); worker.start()
            try:
                base = f'http://127.0.0.1:{server.server_port}/api/items/{item["id"]}/'
                headers = {'Authorization':'Bearer fixture','Content-Type':'application/json'}
                with urlopen(Request(f'http://127.0.0.1:{server.server_port}/api/health',headers=headers)) as response:
                    self.assertNotIn('rating_revision',json.load(response))
                with urlopen(Request(f'http://127.0.0.1:{server.server_port}/api/items',headers=headers)) as response:
                    self.assertNotIn('llm_rating',json.load(response)['items'][0])
                with urlopen(Request(base+'analyze',data=b'{}',headers=headers)) as response:
                    self.assertEqual(response.status,202)
                    self.assertEqual(json.load(response)['queued'],1)
                with urlopen(Request(base+'discuss',data=b'{}',headers=headers)) as response:
                    self.assertEqual(json.load(response)['session_id'],'fixture-session')
            finally:
                server.shutdown(); server.server_close(); worker.join(); store.close()

    def test_current_schema_and_preference_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)/'feed.db', seed=False)
            try:
                tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertNotIn('ratings', tables)
                item, _ = store.add_article({'url':'https://example.org/robot', 'title':'robot coding', 'summary':'implementation'})
                store.react(item['id'], {'rating':1})
                self.assertEqual(store.item(item['id'])['preference_score'],2)
                self.assertEqual(store.list_items('all',q='coding robot')['items'][0]['id'],item['id'])
                self.assertEqual(store.list_items('all',q='robot absent')['total'],0)
            finally:
                store.close()
