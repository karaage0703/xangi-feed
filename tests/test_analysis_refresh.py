"""Read-only editorial refresh and reader-facing validation errors."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request,urlopen

from xangi_feed.enrichment import parse_results
from xangi_feed.server import Server
from xangi_feed.store import Store


class AnalysisRefreshTests(unittest.TestCase):
    def test_validation_errors_use_reader_labels(self):
        valid={'id':'fixture','title_ja':'日本語の見出し','summary_ja':'日本語の要点','comment':'用途のコメント','evidence_ids':[]}
        for key,label in [('title_ja','見出し'),('summary_ja','要点'),('comment','AIコメント')]:
            with self.subTest(field=key):
                for value,expected in [('English only',f'AIが{label}を日本語で生成できませんでした。'),('',f'AIが生成した{label}の形式が正しくありません。')]:
                    with self.assertRaises(ValueError) as error:
                        parse_results(json.dumps([{**valid,key:value}]),{'fixture'})
                    self.assertEqual(str(error.exception),expected)
                    self.assertNotIn(key,str(error.exception))

    def test_batch_refresh_returns_saved_analysis_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'feed',seed=False)
            store.save_settings({'enabled':False})
            item,_=store.add_article({'url':'https://example.org/fixture','title':'Fixture'})
            store.save_analysis(item['id'],store.content_hash(item),'error',error='fixture error')
            original=store.item(item['id'])
            server=Server(store,'fixture');thread=threading.Thread(target=server.serve_forever);thread.start()
            def request(ids):
                req=Request(f'http://127.0.0.1:{server.server_port}/api/analyses?'+urlencode({'id':ids},doseq=True),headers={'Authorization':'Bearer fixture'})
                with urlopen(req) as response:return json.load(response)
            try:
                self.assertEqual(request([item['id'],'missing']),{'analyses':{item['id']:store.analysis(item['id']),'missing':None}})
                for invalid in [[],[''],['x'*129],['x']*101]:
                    with self.assertRaises(HTTPError) as error:request(invalid)
                    self.assertEqual(error.exception.code,400)
                self.assertEqual(store.item(item['id']),original)
                self.assertEqual(server.enricher.status()['queued'],0)
                with store.transaction() as db:
                    db.execute('UPDATE items SET summary=? WHERE id=?',('Changed content',item['id']))
                self.assertEqual(request([item['id']]),{'analyses':{item['id']:None}})
            finally:
                server.shutdown();server.server_close();thread.join();store.close()
