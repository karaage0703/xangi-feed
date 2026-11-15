import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request,urlopen
from xangi_feed.store import Store,RECOMMENDATION_DEFAULTS,VIEW_DEFAULTS
from xangi_feed.server import Server


class SavedViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db'
        self.store=Store(self.path,seed=False)
        self.items={}
        for name in ['unread','read','skip','saved','note','discussion']:
            self.items[name]=self.store.add_article({'url':'https://example.org/'+name,'title':name})[0]
        self.store.react(self.items['read']['id'],{'read':True})
        self.store.react(self.items['skip']['id'],{'muted':True})
        self.store.react(self.items['saved']['id'],{'saved':True})
        self.store.save_note(self.items['note']['id'],'a note')
        self.store.discussion(self.items['discussion']['id'],'sent','fixture-session')

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def view(self,conditions=None):
        return self.store.save_custom_filter({'name':'custom','recommendations':dict(RECOMMENDATION_DEFAULTS),'conditions':conditions or {}})

    def titles(self,record):
        return {item['title'] for item in self.store.list_items(custom_filter=record['id'])['items']}

    def test_seeded_presets_are_real_editable_views_and_migrate_once(self):
        old=self.view();before=dict(old)
        self.store.save_custom_classification({'id':'','kinds':['podcast'],'topics':[],'tags':[],'source':'','search':'old filter','sort':'preference'})
        views=self.store.ensure_custom_views()
        self.assertEqual(len(views),10)
        self.assertEqual(self.store.custom_filter(old['id']),before)
        default=self.store.custom_filter('preset-recommended')
        self.assertEqual(default['kinds'],['podcast']);self.assertEqual(default['search'],'old filter')
        read=self.store.custom_filter('preset-read');read['conditions']['read']='any'
        self.store.save_custom_filter(read)
        self.store.delete_custom_filter('preset-muted')
        order=[v['id'] for v in self.store.custom_filters()][::-1]
        self.store.reorder_custom_filters(order)
        self.store.close();self.store=Store(self.path,seed=False)
        self.assertEqual([v['id'] for v in self.store.ensure_custom_views()],order)
        self.assertEqual(self.store.custom_filter('preset-read')['conditions']['read'],'any')
        # Older clients may rename a view without knowing the new conditions field.
        old_payload=dict(self.store.custom_filter('preset-read'));old_payload.pop('conditions');old_payload['name']='old client edit'
        self.assertEqual(self.store.save_custom_filter(old_payload)['conditions']['read'],'any')
        self.assertNotIn('preset-muted',order)

    def test_status_combinations_and_legacy_presets(self):
        self.store.ensure_custom_views()
        expected={'preset-read':{'read'},'preset-muted':{'skip'},'preset-saved':{'saved'},'preset-notes':{'note'},'preset-discussed':{'discussion'},'preset-all':set(self.items),'preset-recommended':set(self.items)-{'read','skip'}}
        for view_id,titles in expected.items():
            with self.subTest(view=view_id):self.assertEqual(self.titles({'id':view_id}),titles)
        self.assertEqual(self.titles(self.view({'read':'read','muted':'only'})),set())
        self.assertEqual(self.titles(self.view({'read':'any','muted':'include','saved':'no'})),set(self.items)-{'saved'})
        self.assertEqual(self.titles(self.view({'read':'any','noted':'yes','discussed':'yes'})),set())

    def test_upper_preference_and_invalid_ranges(self):
        item=self.items['saved'];self.store.react(item['id'],{'rating':1})
        record=self.view({'read':'any','max_preference':100})
        self.assertIn('saved',self.titles(record))
        self.assertNotIn('unread',self.titles(record))
        record['conditions']['max_preference']=-100
        self.store.save_custom_filter(record);self.assertNotIn('saved',self.titles(record))
        for conditions in [{'read':'wrong'},{'muted':True},{'max_preference':True},{'max_preference':101},{'unknown':'any'}]:
            with self.subTest(conditions=conditions),self.assertRaises(ValueError):self.view(conditions)
        record['recommendations']['recommendation_min_preference']=0
        with self.assertRaises(ValueError):self.store.save_custom_filter(record)

    def test_saved_classification_and_sort_are_applied_by_the_server(self):
        record=self.view({'read':'any','muted':'include'})
        record.update({'search':'discussion','sort':'read_at'})
        self.store.save_custom_filter(record)
        self.assertEqual(self.titles(record),{'discussion'})
        record['tags']=['missing'];self.store.save_custom_filter(record)
        self.assertEqual(self.titles(record),set())

    def test_rename_only_preserves_every_saved_filter(self):
        record=self.view({'read':'any','muted':'only','max_preference':90})
        record.update({'kinds':['podcast'],'topics':['ロボット'],'tags':['自由タグ'],'source':'12','search':'keyword','sort':'preference'})
        record=self.store.save_custom_filter(record)
        renamed=self.store.rename_custom_view(record['id'],'new name')
        self.assertEqual({**record,'name':'new name'},renamed)
        for name in ['', 'a'*81, None]:
            with self.assertRaises(ValueError):self.store.rename_custom_view(record['id'],name)
        self.assertEqual(self.store.custom_filter(record['id']),renamed)

    def test_temporary_search_intersects_saved_query_without_saving(self):
        record=self.view({'read':'any','muted':'include'})
        record['search']='read';record=self.store.save_custom_filter(record)
        original=self.store.custom_filter(record['id'])
        result=self.store.list_items(custom_filter=record['id'],search_query='unread',limit=1)
        self.assertEqual(result['total'],1);self.assertEqual(result['items'][0]['title'],'unread')
        self.assertEqual(self.store.custom_filter(record['id']),original)
        self.assertEqual(self.store.list_items(custom_filter=record['id'])['total'],2)
        self.assertEqual(self.store.list_items(custom_filter=record['id'],search_query='missing')['total'],0)
        for invalid in [True,'a'*201]:
            with self.assertRaises(ValueError):self.store.list_items(custom_filter=record['id'],search_query=invalid)

    def test_max_existing_custom_filters_can_migrate_and_reorder(self):
        for i in range(100):self.view()
        views=self.store.ensure_custom_views();self.assertEqual(len(views),109)
        self.store.reorder_custom_filters([v['id'] for v in views][::-1])
        self.assertEqual(self.store.custom_filters()[0]['id'],views[-1]['id'])
        with self.assertRaises(ValueError):self.view()

    def test_api_updates_all_filters_without_touching_other_views(self):
        server=Server(self.store,'fixture');worker=threading.Thread(target=server.serve_forever);worker.start()
        def request(path,data=None):
            req=Request(f'http://127.0.0.1:{server.server_port}/api/'+path,headers={'Authorization':'Bearer fixture','Content-Type':'application/json'},data=None if data is None else json.dumps(data).encode())
            with urlopen(req) as response:return json.load(response)
        try:
            original=request('views')['filters'];read=next(v for v in original if v['id']=='preset-read')
            values={k:read[k] for k in ['id','conditions','recommendations','kinds','topics','tags','source','search','sort']}
            values['conditions']={**values['conditions'],'read':'any','muted':'include','max_preference':90}
            saved=request('views/save',values)['filter']
            self.assertEqual(saved['name'],'読了');self.assertEqual(saved['conditions']['max_preference'],90)
            renamed=request('views/rename',{'id':saved['id'],'name':'renamed'})['filter']
            self.assertEqual(renamed,{**saved,'name':'renamed'})
            updated=request('views')['filters']
            self.assertEqual([v for v in updated if v['id']!='preset-read'],[v for v in original if v['id']!='preset-read'])
        finally:
            server.shutdown();server.server_close();worker.join()

    def test_single_item_filter_uses_the_same_rules_without_materializing_all_items(self):
        conditions=[{'read':'any','muted':'include'},{'read':'unread'},{'read':'read'},{'muted':'only'},{'saved':'yes'},{'saved':'no'},{'noted':'yes'},{'discussed':'yes'},{'origin':'manual'}]
        for condition in conditions:
            record=self.view(condition)
            for search in ['', 'read', 'a note', 'missing']:
                expected={item['id'] for item in self.store.list_items(custom_filter=record['id'],search_query=search)['items']}
                for item in self.items.values():
                    actual=self.store.list_items(custom_filter=record['id'],search_query=search,only_ids=[item['id']])
                    self.assertEqual(actual['total'],int(item['id'] in expected))
        queries=[];self.store.db.set_trace_callback(queries.append)
        try:self.store.list_items(custom_filter=record['id'],only_ids=[self.items['saved']['id']])
        finally:self.store.db.set_trace_callback(None)
        self.assertTrue(any('SELECT * FROM items WHERE id IN' in query for query in queries))
        self.assertFalse(any(query.strip()=='SELECT * FROM items' for query in queries))

    def test_reaction_returns_membership_and_invalid_view_does_not_mutate(self):
        server=Server(self.store,'fixture');worker=threading.Thread(target=server.serve_forever);worker.start()
        from urllib.error import HTTPError
        from urllib.parse import urlencode
        def react(view,data,search=''):
            req=Request(f'http://127.0.0.1:{server.server_port}/api/items/{self.items["unread"]["id"]}/react?'+urlencode({'custom_filter':view,'search':search}),headers={'Authorization':'Bearer fixture','Content-Type':'application/json'},data=json.dumps(data).encode())
            with urlopen(req) as response:return json.load(response)
        try:
            record=self.view({'read':'unread','muted':'include'})
            self.assertTrue(react(record['id'],{'saved':True})['view_matches'])
            self.assertFalse(react(record['id'],{'read':True})['view_matches'])
            self.assertFalse(react(record['id'],{'read':False},'missing')['view_matches'])
            with self.assertRaises(HTTPError):react('unknown',{'saved':False})
            self.assertTrue(self.store.item(self.items['unread']['id'])['saved'])
            with self.assertRaises(HTTPError):react(record['id'],{'saved':False},'a'*201)
            self.assertTrue(self.store.item(self.items['unread']['id'])['saved'])
        finally:server.shutdown();server.server_close();worker.join()
