"""One source list, with adapters for the existing skill input formats."""
import json
import os
from pathlib import Path

from .feeds import web_url

BUNDLED = Path(__file__).resolve().parent.parent / 'catalog.json'


def load(path=None):
    selected = Path(path or os.environ.get('XANGI_FEED_CATALOG') or BUNDLED)
    data = json.loads(selected.read_text())
    if data.get('schema_version') != 1 or not isinstance(data.get('sources'), list):
        raise ValueError('配信元カタログの形式が不正です')
    seen_ids, seen_urls = set(), set()
    for row in data['sources']:
        url = web_url(row['url'])
        if row['id'] in seen_ids or url in seen_urls:
            raise ValueError('配信元カタログに重複があります')
        if not isinstance(row['name'], str) or not row['name'] or not isinstance(row.get('consumers'), list):
            raise ValueError('配信元カタログの項目が不正です')
        seen_ids.add(row['id']); seen_urls.add(url)
    return data['sources']


def export(consumer, path=None):
    rows = [r for r in load(path) if consumer in r['consumers']]
    if consumer == 'feed':
        return [{k: r.get(k, 'rss') for k in ['name', 'url', 'kind', 'adapter']} for r in rows]
    if consumer == 'patrol':
        return {'sites': [{'name': r['name'], 'url': r['url'], **r.get('patrol', {'type': 'rss', 'category': r['kind']})} for r in rows]}
    if consumer == 'podcast':
        return [dict(r['podcast']) for r in rows]
    if consumer == 'technews':
        return [{'name': r['name'], 'url': r['url']} for r in rows]
    raise ValueError('consumerはfeed/patrol/podcast/technewsです')
