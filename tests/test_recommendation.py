import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from xangi_feed.recommendation import search_evidence
from xangi_feed.server import Server
from xangi_feed.store import Store


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.host = Mock(configured=True)
        self.item = {'title': '音声対話ロボット & マイク', 'url': 'https://untrusted.example/search'}

    def respond(self, data):
        self.host.request.side_effect = lambda *a, **kw: io.BytesIO(json.dumps(data).encode())

    def test_context_is_returned_without_llm_judgement_or_scores(self):
        self.respond({'results': [{'file_path': 'projects/voice.md', 'content': '音声対話の試作記録', 'score': .99}]})
        result = search_evidence(self.host, self.item)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['sources'], [{'path': 'projects/voice.md', 'excerpt': '音声対話の試作記録'}])
        path = self.host.request.call_args.args[0]
        self.assertTrue(path.startswith('/api/extensions/xangi-search/service/search?'))
        self.assertEqual(parse_qs(urlsplit(path).query)['q'], [self.item['title']])
        self.assertEqual(self.host.request.call_args.kwargs['timeout'], 10)
        self.host.run.assert_not_called()

    def test_empty_unknown_and_disconnected_are_distinct(self):
        self.respond({'results': []})
        self.assertEqual(search_evidence(self.host, self.item)['status'], 'empty')
        self.host.configured = False
        self.host.request.reset_mock()
        self.assertEqual(search_evidence(self.host, self.item)['status'], 'unavailable')
        self.host.request.assert_not_called()

    def test_degraded_search_is_disclosed(self):
        self.respond({'degraded': True, 'results': [{'file_path': 'notes.md', 'content': '関連記録'}]})
        result = search_evidence(self.host, self.item)
        self.assertTrue(result['degraded'])
        self.assertIn('キーワード検索', result['message'])

    def test_failure_is_safe_and_retry_is_possible(self):
        self.host.request.side_effect = TimeoutError('secret-token-private-host')
        result = search_evidence(self.host, self.item)
        self.assertEqual(result['status'], 'error')
        self.assertNotIn('secret', json.dumps(result))
        self.respond({'results': []})
        self.assertEqual(search_evidence(self.host, self.item)['status'], 'empty')

    def test_invalid_or_oversized_results_are_not_used_as_evidence(self):
        for data in [{'results': {}}, {'results': [None]}, {'results': [{'file_path': 'missing-excerpt'}]}]:
            self.respond(data)
            self.assertEqual(search_evidence(self.host, self.item)['status'], 'error')
        self.host.request.side_effect = lambda *a, **kw: io.BytesIO(b'x' * (256 * 1024 + 1))
        self.assertEqual(search_evidence(self.host, self.item)['status'], 'error')

    def test_sources_are_bounded_and_deduplicated(self):
        self.respond({'results': [{'file_path': str(i // 2), 'content': '<script>untrusted</script>' * 1000} for i in range(10)]})
        result = search_evidence(self.host, self.item)
        self.assertEqual(len(result['sources']), 3)
        self.assertTrue(all(len(s['excerpt']) == 1200 for s in result['sources']))

    def test_api_is_authenticated_and_search_cannot_change_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'feed.db', seed=False)
            item = store.add_article({'url': 'https://example.org/robot', 'title': self.item['title']})[0]
            store.react(item['id'], {'rating': 1})
            before = store.profile()
            self.respond({'results': [{'file_path': 'project.md', 'content': '試作記録'}]})
            server = Server(store, 'fixture-token', host=self.host)
            thread = threading.Thread(target=server.serve_forever); thread.start()
            try:
                url = f'http://127.0.0.1:{server.server_port}/api/items/{item["id"]}/evidence'
                with self.assertRaises(HTTPError) as error:
                    urlopen(url)
                self.assertEqual(error.exception.code, 401)
                self.host.request.assert_not_called()
                with urlopen(Request(url, headers={'Authorization': 'Bearer fixture-token'})) as response:
                    self.assertEqual(json.load(response)['status'], 'ready')
                self.assertEqual(store.profile(), before)
                self.assertEqual(store.item(item['id'])['rating'], 1)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(url.replace(item['id'], 'missing'), headers={'Authorization': 'Bearer fixture-token'}))
                self.assertEqual(error.exception.code, 404)
                self.host.run.assert_not_called()
            finally:
                server.shutdown(); server.server_close(); thread.join()
                server.collector.close(); server.enricher.close(); store.close()


if __name__ == '__main__':
    unittest.main()
