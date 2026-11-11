"""Retrieve recommendation context without judging or changing user preferences."""
import json
from urllib.parse import urlencode


def search_evidence(host, item):
    query = item['title'].strip()[:240]
    if not host.configured:
        return {'status': 'unavailable', 'query': query, 'sources': [],
                'message': 'xangi-searchへの接続が設定されていません。'}
    if not query:
        return {'status': 'empty', 'query': query, 'sources': [], 'message': '検索に使えるタイトルがありません。'}
    try:
        params = urlencode({'q': query, 'mode': 'hybrid', 'k': 3, 's': 0.3, 'forgetting': 'false'})
        with host.request('/api/extensions/xangi-search/service/search?' + params, timeout=10) as response:
            raw = response.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError('oversized search response')
        payload = json.loads(raw)
        results = payload['results']
        if not isinstance(results, list):
            raise ValueError('invalid search response')
        sources, seen = [], set()
        for row in results:
            path, content = row.get('file_path'), row.get('content')
            if not isinstance(path, str) or not path or not isinstance(content, str) or not content.strip():
                raise ValueError('invalid search result')
            if path not in seen:
                sources.append({'path': path[:1000], 'excerpt': content[:1200]})
                seen.add(path)
            if len(sources) == 3:
                break
        degraded = bool(payload.get('degraded') or payload.get('degraded_reason'))
        message = ('記事に関連する記録です。好みはGood／Badから集計します。' if sources else
                   '関連する記録は見つかりませんでした。好みの低評価にはしません。')
        if degraded:
            message = '意味検索を利用できないため、キーワード検索の結果を表示しています。' + message
        return {'status': 'ready' if sources else 'empty', 'query': query, 'sources': sources,
                'degraded': degraded, 'message': message}
    except Exception:
        # Never expose host URLs, credentials or upstream error bodies to the UI.
        return {'status': 'error', 'query': query, 'sources': [],
                'message': '推薦根拠を取得できませんでした。xangi-searchの接続・稼働状態を確認してください。'}
