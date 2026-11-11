"""Find public feeds from RSS search results without asking an LLM to browse."""
import hashlib
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit

from .feeds import _retrieve, MAX_BYTES, parse_feed, topics, web_url
from .discovery_prompt import japanese

SEARCH_TERMS = {'科学': 'science research', '音声': 'audio podcast', 'AI・LLM': 'artificial intelligence LLM',
                'エージェント': 'AI agents', 'ロボット': 'robotics', 'ものづくり': 'maker electronics',
                '開発': 'software development', 'ローカルAI': 'local LLM'}


def scope(url):
    p = urlsplit(web_url(url))
    host = p.hostname.lower().removeprefix('www.')
    # Independent publications sharing one hosting service are separate sources.
    if host in {'note.com', 'zenn.dev', 'qiita.com', 'medium.com', 'substack.com'}:
        return host + '/' + p.path.strip('/').split('/')[0]
    return host


class FeedLinks(HTMLParser):
    def __init__(self, url):
        super().__init__(); self.url = url; self.links = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'link' and 'alternate' in a.get('rel', '').lower().split() and a.get('type', '').lower().split(';')[0] in {'application/rss+xml', 'application/atom+xml'}:
            try:
                link = web_url(urljoin(self.url, a.get('href', '')))
                if link not in self.links: self.links.append(link)
            except ValueError:
                pass


def search_pages(names, fetcher, diagnostics, active=lambda: True, plan=None):
    pages = []
    plan = plan or {}
    japanese_only = plan.get('language') == 'ja'
    for name in (plan.get('queries') or names)[:3]:
        query = name if plan.get('queries') or japanese_only else SEARCH_TERMS.get(name, name)
        for engine, url in [
            ('Bing RSS', 'https://www.bing.com/search?' + urlencode({'q': query + (' 記事 RSS' if japanese_only else ' blog RSS'), 'format': 'rss', **({'setlang':'ja-JP','cc':'JP'} if japanese_only else {})})),
            ('Google News RSS', 'https://news.google.com/rss/search?' + urlencode({'q': query, 'hl': 'ja' if japanese_only else 'en-US', 'gl': 'JP' if japanese_only else 'US', 'ceid': 'JP:ja' if japanese_only else 'US:en'})),
        ]:
            if not active(): return pages
            try:
                data = fetcher(url)
                # Use the same DTD/entity/size validation as every other feed.
                entries = parse_feed(data, url)
                found = [e['url'] for e in entries] if engine == 'Bing RSS' else [s.get('url') for s in ET.fromstring(data).iter('source') if s.get('url')]
                for value in found:
                    try:
                        value = web_url(value)
                        if value not in pages: pages.append(value)
                    except ValueError:
                        pass
                diagnostics['searches_ok'] += 1
            except Exception:
                diagnostics['searches_failed'] += 1
    return pages


def fetch_public(url):
    return _retrieve(url, MAX_BYTES, timeout=5, deadline_seconds=10)


def discover(positive, negative, registered, fetcher=None, now=None, stop=None, budget=90, plan=None):
    plan = plan or {'language':'any', 'exclude_terms':[], 'max_age_days':30, 'queries':[]}
    fetcher = fetcher or fetch_public
    now = time.time() if now is None else now
    weights = {r['name']: r['weight'] for r in positive + negative}
    deadline = time.monotonic() + budget
    diagnostics = {'searches_ok': 0, 'searches_failed': 0, 'sites_checked': 0, 'feeds_checked': 0,
                   'registered_skipped': 0, 'unmatched_or_old': 0, 'fetch_failed': 0}
    names = {r['name'] for r in positive}
    excluded = {scope(u) for u in registered}
    def active(): return time.monotonic() < deadline and not (stop and stop.is_set())
    # Search calls run in the same bounded worker group as site requests.
    pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix='feed-discovery-http')
    def inspect(page):
        stats = {'sites_checked': 1, 'feeds_checked': 0, 'unmatched_or_old': 0, 'fetch_failed': 0}
        if not active(): return None, stats
        urls = []
        try:
            data = fetcher(page)
            try:
                parse_feed(data, page); urls.append(page)
            except Exception:
                parser = FeedLinks(page); parser.feed(data.decode('utf-8', errors='replace')); urls.extend(parser.links[:3])
        except Exception:
            stats['fetch_failed'] += 1
        origin = urlsplit(page)
        root = origin.scheme + '://' + origin.netloc
        if not urls:
            # These are probes, never offered until an actual feed is parsed.
            urls.extend([root + '/feed', root + '/rss', root + '/rss.xml'])
        for url in urls[:3]:
            if not active() or scope(url) in excluded: continue
            stats['feeds_checked'] += 1
            try:
                entries = parse_feed(fetcher(url), url)
                for entry in entries:
                    article_text = entry['title'] + ' ' + entry['summary']
                    if plan.get('language') == 'ja' and not japanese(article_text): continue
                    if any(word.casefold() in article_text.casefold() for word in plan.get('exclude_terms', [])): continue
                    date = entry.get('published') or entry.get('updated')
                    entry_topics = topics(entry['title'] + ' ' + entry['summary'])
                    matched = sorted(names.intersection(entry_topics))
                    query_match = any(all(term in article_text.casefold() for term in query.casefold().split()) for query in plan.get('queries', []) if query.strip())
                    if not query_match and sum(weights.get(t, 0) for t in entry_topics) <= 0: continue
                    if not date or not now - plan.get('max_age_days',30) * 86400 <= date <= now + 86400 or (not matched and not query_match) or scope(entry['url']) in excluded:
                        stats['unmatched_or_old'] += 1; continue
                    return {'id': hashlib.sha256(url.encode()).hexdigest()[:24], 'feed_url': url,
                            'url': entry['url'], 'title': entry['title'], 'language': 'ja' if japanese(article_text) else 'other', 'source_name': entry.get('publication_name') or scope(url),
                            'kind': 'podcast' if entry.get('audio_url') else 'blog', 'reason': ('検索語に一致する最近の記事が公開フィードにあります。' if query_match else 'Goodで関心のある「' + '・'.join(matched) + '」に一致する最近の記事が公開フィードにあります。'),
                            'matched_topics': matched, 'published': date, 'verified_at': now}, stats
            except Exception:
                stats['fetch_failed'] += 1
        return None, stats
    try:
        future = pool.submit(search_pages, [r['name'] for r in positive], fetcher, diagnostics, active, plan)
        done, _ = wait([future], timeout=max(0, deadline-time.monotonic()))
        if not done: raise TimeoutError('search deadline')
        pages = future.result(); selected = []; seen = set()
        for page in pages:
            key = scope(page)
            if key.endswith('wikipedia.org') or key in {'www.bing.com', 'bing.com', 'news.google.com'}: continue
            if key in excluded: diagnostics['registered_skipped'] += 1; continue
            if key not in seen: selected.append(page); seen.add(key)
            if len(selected) >= 30: break
        pending = {pool.submit(inspect, page) for page in selected}; items = []
        while pending and active():
            done, pending = wait(pending, timeout=max(0, deadline-time.monotonic()), return_when=FIRST_COMPLETED)
            for f in done:
                item, stats = f.result()
                for key, count in stats.items(): diagnostics[key] += count
                if item and scope(item['feed_url']) not in {scope(i['feed_url']) for i in items}: items.append(item)
            if len(items) >= 6: break
        diagnostics['deadline_reached'] = bool(pending and len(items) < 6)
        weights = {r['name']: r['weight'] for r in positive + negative}
        items.sort(key=lambda item: (sum(weights.get(t, 0) for t in item['matched_topics']), item['published']), reverse=True)
        return items[:6], diagnostics
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
