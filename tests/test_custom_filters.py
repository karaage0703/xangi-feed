import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from xangi_feed.store import Store, RECOMMENDATION_DEFAULTS
from xangi_feed.server import Server


class CustomFilterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'test.db'
        self.store = Store(self.path, seed=False)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def payload(self, name='Podcast'):
        return {'name': name, 'kinds': ['podcast'], 'recommendations': dict(RECOMMENDATION_DEFAULTS), 'sort': 'preference', 'search': 'robot'}

    def test_independent_records_survive_reopen_update_and_delete(self):
        first = self.store.save_custom_filter(self.payload())
        second = self.store.save_custom_filter(self.payload('好みが高い'))
        second['recommendations']['recommendation_min_preference'] = 5
        self.store.save_custom_filter(second)
        self.store.close()
        self.store = Store(self.path, seed=False)
        self.assertEqual(len(self.store.custom_filters()), 2)
        self.assertIsNone(self.store.custom_filter(first['id'])['recommendations']['recommendation_min_preference'])
        self.assertEqual(self.store.custom_filter(second['id'])['recommendations']['recommendation_min_preference'], 5)
        self.store.delete_custom_filter(first['id'])
        with self.assertRaises(KeyError):
            self.store.save_custom_filter(first)
        self.assertEqual(self.store.custom_filters(), [second])

    def test_selected_recommendations_do_not_modify_legacy_settings(self):
        item, _ = self.store.add_article({'url': 'https://example.org/test', 'title': 'robot'})
        self.store.react(item['id'], {'rating': -1})
        data = self.payload()
        data['recommendations']['recommendation_hide_bad'] = True
        saved = self.store.save_custom_filter(data)
        self.assertEqual(self.store.list_items(custom_filter=saved['id'])['total'], 0)
        self.assertEqual(self.store.list_items()['total'], 1)
        self.assertFalse(self.store.settings()['recommendation_hide_bad'])
        self.assertEqual(self.store.list_items('all', custom_filter=saved['id'])['total'], 0)

    def test_validation_is_atomic(self):
        for patch in [{'name': ''}, {'name': 'a' * 81}, {'kinds': ['wrong']}, {'tags': [None]}, {'sort': []}, {'search': 2}, {'recommendations': {}}, {'id': 5}, {'unexpected': True}]:
            with self.subTest(patch=patch), self.assertRaises((ValueError, KeyError)):
                self.store.save_custom_filter({**self.payload(), **patch})
        for key, value in [('recommendation_days', True), ('recommendation_min_preference', 101), ('recommendation_hide_bad', 1), ('recommendation_priority', [])]:
            payload = self.payload()
            payload['recommendations'][key] = value
            with self.assertRaises(ValueError):
                self.store.save_custom_filter(payload)
        self.assertEqual(self.store.custom_filters(), [])

    def test_limit_does_not_block_updates_and_allows_create_after_delete(self):
        records = [self.store.save_custom_filter(self.payload(str(i))) for i in range(100)]
        with self.assertRaises(ValueError):
            self.store.save_custom_filter(self.payload('over limit'))
        records[0]['name'] = '更新可能'
        self.store.save_custom_filter(records[0])
        self.store.delete_custom_filter(records[1]['id'])
        self.store.save_custom_filter(self.payload('replacement'))
        self.assertEqual(len(self.store.custom_filters()), 100)

    def test_order_survives_reopen_rename_add_and_delete(self):
        records = [self.store.save_custom_filter(self.payload(str(i))) for i in range(3)]
        ids = [record['id'] for record in records]
        self.assertEqual([r['id'] for r in self.store.custom_filters()], ids)
        self.store.reorder_custom_filters(ids[::-1])
        records[1]['name'] = 'renamed'
        self.store.save_custom_filter(records[1])
        self.store.close(); self.store = Store(self.path, seed=False)
        self.assertEqual([r['id'] for r in self.store.custom_filters()], ids[::-1])
        extra = self.store.save_custom_filter(self.payload('new'))
        self.assertEqual([r['id'] for r in self.store.custom_filters()], ids[::-1] + [extra['id']])
        self.store.delete_custom_filter(ids[1])
        self.assertEqual([r['id'] for r in self.store.custom_filters()], [ids[2], ids[0], extra['id']])

    def test_invalid_or_stale_order_keeps_every_record(self):
        records = [self.store.save_custom_filter(self.payload(str(i))) for i in range(2)]
        ids = [record['id'] for record in records]
        for invalid in [None, 'wrong', [None], [ids[0]], ids + ['missing'], [ids[0], ids[0]]]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.reorder_custom_filters(invalid)
            self.assertEqual(self.store.custom_filters(), records)

    def test_legacy_filters_without_order_are_kept(self):
        records = [self.store.save_custom_filter(self.payload(str(i))) for i in range(2)]
        with self.store.transaction() as db:
            db.execute("DELETE FROM metadata WHERE key='custom-filter-order'")
        legacy = self.store.custom_filters()
        self.assertEqual({r['id'] for r in legacy}, {r['id'] for r in records})
        self.store.reorder_custom_filters([r['id'] for r in legacy[::-1]])
        self.assertEqual(self.store.custom_filters(), legacy[::-1])

    def classification(self, filter_id=''):
        return {'id': filter_id, 'kinds': ['podcast'], 'topics': ['ロボット'], 'tags': ['音声'], 'source': '1', 'search': 'robot', 'sort': 'preference'}

    def test_classification_only_update_keeps_name_recommendations_and_order(self):
        first = self.store.save_custom_filter(self.payload('first'))
        other = self.store.save_custom_filter(self.payload('other'))
        self.store.reorder_custom_filters([other['id'], first['id']])
        first['name'] = 'another computer renamed'
        first['recommendations']['recommendation_days'] = 30
        self.store.save_custom_filter(first)
        result = self.store.save_custom_classification(self.classification(first['id']))
        self.assertEqual(result['filter']['name'], first['name'])
        self.assertEqual(result['filter']['recommendations']['recommendation_days'], 30)
        self.assertEqual(result['filter']['kinds'], ['podcast'])
        self.assertEqual([r['id'] for r in self.store.custom_filters()], [other['id'], first['id']])
        self.assertEqual(self.store.custom_filter(other['id']), other)

    def test_default_classification_survives_reopen_without_creating_a_tab(self):
        before = self.store.settings()
        result = self.store.save_custom_classification(self.classification())
        self.assertIsNone(result['filter'])
        self.store.close(); self.store = Store(self.path, seed=False)
        expected = self.classification(); expected.pop('id')
        self.assertEqual(self.store.default_custom_classification(), expected)
        self.assertEqual(self.store.custom_filters(), [])
        self.assertEqual(self.store.settings(), before)

    def test_invalid_or_deleted_classification_is_not_saved(self):
        for patch in [{'kinds': ['wrong']}, {'sort': 'invalid'}, {'id': None}, {'source': 1}, {'unknown': True}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.store.save_custom_classification({**self.classification(), **patch})
        with self.assertRaises(KeyError):
            self.store.save_custom_classification(self.classification('deleted'))
        self.assertIsNone(self.store.default_custom_classification())

    def test_api_auth_crud_and_delete_confirmation(self):
        server = Server(self.store, 'fixture-token')
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        def request(path='', data=None, token='fixture-token'):
            headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
            req = Request(f'http://127.0.0.1:{server.server_port}/api/custom-filters' + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
            with urlopen(req) as response:
                return json.load(response)
        try:
            with self.assertRaises(HTTPError) as error:
                request(token='wrong')
            self.assertEqual(error.exception.code, 401)
            saved = request(data=self.payload())
            self.assertEqual(request()['filters'], [saved])
            self.assertIsNone(request()['default_classification'])
            request('/classification', self.classification())
            self.assertEqual(request()['default_classification']['kinds'], ['podcast'])
            request('/classification', self.classification(saved['id']))
            saved = self.store.custom_filter(saved['id'])
            other = request(data=self.payload('second'))
            self.assertEqual(request('/reorder', {'ids': [other['id'], saved['id']]})['filters'], [other, saved])
            with self.assertRaises(HTTPError) as reorder_error:
                request('/reorder', {'ids': [saved['id']]})
            self.assertEqual(reorder_error.exception.code, 400)
            request('/delete', {'id': other['id'], 'confirm': True})
            with self.assertRaises(HTTPError) as error:
                request('/delete', {'id': saved['id'], 'confirm': False})
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(request('/delete', {'id': saved['id'], 'confirm': True}), {'deleted': True})
            self.assertEqual(request()['filters'], [])
        finally:
            server.shutdown(); server.server_close(); worker.join()
