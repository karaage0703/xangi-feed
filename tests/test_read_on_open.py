import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from xangi_feed.store import Store
from xangi_feed.server import Server


class ReadOnOpenTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'feed.db'
        self.store=Store(self.path,seed=False)
        self.item=self.store.add_article({'url':'https://example.org/a'})[0]['id']

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def test_default_on_and_idempotent(self):
        self.assertTrue(self.store.settings()['mark_read_on_open'])
        self.assertTrue(self.store.open_item(self.item)['marked_read'])
        self.assertTrue(self.store.item(self.item)['read'])
        self.assertFalse(self.store.open_item(self.item)['marked_read'])
        self.assertEqual(self.store.list_items()['total'],0)
        self.assertEqual(self.store.list_items(tab='all')['total'],1)

    def test_off_persists_and_manual_read_still_works(self):
        self.store.save_settings({'mark_read_on_open':False})
        self.store.close();self.store=Store(self.path,seed=False)
        self.assertFalse(self.store.settings()['mark_read_on_open'])
        self.assertFalse(self.store.open_item(self.item)['marked_read'])
        self.assertFalse(self.store.item(self.item)['read'])
        self.store.react(self.item,{'read':True})
        self.assertTrue(self.store.open_item(self.item)['item']['read'])
        self.store.react(self.item,{'read':False})
        self.assertFalse(self.store.open_item(self.item)['item']['read'])

    def test_existing_settings_default_and_validation(self):
        with self.store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('settings',?)",(json.dumps({'enabled':False}),))
        self.assertTrue(self.store.settings()['mark_read_on_open'])
        self.assertFalse(self.store.item(self.item)['read'])
        self.assertFalse(self.store.settings()['enabled'])
        for value in [1,None,'false']:
            with self.subTest(value=value),self.assertRaises(ValueError):self.store.save_settings({'mark_read_on_open':value})

    def test_open_preserves_user_data_tags_and_analysis(self):
        self.store.save_note(self.item,'保持するメモ');self.store.change_tag(self.item,'add','工作')
        self.store.react(self.item,{'saved':True,'rating':1});self.store.discussion(self.item,'sent','session-1')
        self.store.save_analysis(self.item,self.store.content_hash(self.store.item(self.item)),'done',{'summary_ja': '保持する要約'})
        profile=self.store.profile();self.store.open_item(self.item);item=self.store.item(self.item)
        self.assertEqual((item['note'],item['tags'],item['rating'],item['saved'],item['session_id']),('保持するメモ',['工作'],1,1,'session-1'))
        self.assertEqual(item['analysis']['status'],'done');self.assertEqual(self.store.profile(),profile)

    def test_http_open_uses_server_policy_auth_and_validation(self):
        server=Server(self.store,'fixture-'*8);thread=threading.Thread(target=server.serve_forever);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        def post(path,data,auth=True):
            headers={'Content-Type':'application/json'}
            if auth:headers['Authorization']='Bearer '+'fixture-'*8
            return urllib.request.urlopen(urllib.request.Request(origin+path,data=json.dumps(data).encode(),headers=headers))
        endpoint=f'/api/items/{self.item}/open'
        try:
            for data,auth,code in [({},False,401),({'read':True},True,400)]:
                with self.assertRaises(urllib.error.HTTPError) as error:post(endpoint,data,auth)
                self.assertEqual(error.exception.code,code)
            self.store.save_settings({'mark_read_on_open':False})
            with post(endpoint,{}) as response:self.assertFalse(json.load(response)['marked_read'])
            self.store.save_settings({'mark_read_on_open':True})
            with post(endpoint,{}) as response:self.assertTrue(json.load(response)['marked_read'])
        finally:
            server.shutdown();server.server_close();thread.join();server.collector.close();server.enricher.close()
