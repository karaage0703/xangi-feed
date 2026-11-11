"""Display source-provided publication identity, with an explicit domain fallback."""
from urllib.parse import urlsplit

GENERIC_NAMES = {'はてなブログ', 'hatena blog', 'youtube', 'note', 'qiita', 'zenn', 'rss', 'ブログ', '記事'}


def clean(value, limit=300):
    return ' '.join(str(value or '').split())[:limit]


def label(item):
    publication = clean(item.get('publication_name'))
    author = clean(item.get('author'))
    names = [publication] if publication and publication.casefold() not in GENERIC_NAMES else []
    if not names:
        names = [clean(s['name']) for s in item.get('sources', [])
                 if clean(s['name']).casefold() not in GENERIC_NAMES]
    names = list(dict.fromkeys(name for name in names if name))
    if author and author not in names:
        names.append('著者: ' + author)
    url = urlsplit(item['url'])
    host = url.hostname or ''
    parts = [part for part in url.path.split('/') if part]
    shared_sites = {'note.com': {'n'}, 'qiita.com': {'items'}, 'zenn.dev': {'articles', 'books', 'scraps'}}
    if host in shared_sites and len(parts) >= 3 and parts[1] in shared_sites[host]:
        host += '/' + parts[0]
    if names:
        return ' / '.join(names) + ('（' + host + '）' if host else '')
    return host or '配信元不明'
