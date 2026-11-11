"""Read-only adapter check: public HTTP only, no database or model calls."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xangi_feed.adapters import ADAPTERS, parse_page, validate
from xangi_feed.feeds import fetch_feed, parse_feed
from xangi_feed.youtube import resolve, fetch_entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', choices=['rss', 'youtube', *ADAPTERS], default='rss')
    parser.add_argument('--url', required=True)
    args = parser.parse_args()
    try:
        if args.adapter == 'youtube':
            entries = fetch_entries(resolve(args.url))
        else:
            validate(args.adapter, args.url)
            data = fetch_feed(args.url)
            entries = parse_feed(data, args.url) if args.adapter == 'rss' else parse_page(args.adapter, data, args.url)
        dates = [e['published'] for e in entries if e['published'] is not None]
        print(json.dumps({'url': args.url, 'adapter': args.adapter, 'ok': True, 'count': len(entries),
                          'undated': len(entries) - len(dates), 'latest_published_utc': datetime.fromtimestamp(max(dates), timezone.utc).isoformat() if dates else None,
                          'items': [{'title': e['title'], 'url': e['url'], 'published': e['published']} for e in entries[:5]]}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'url': args.url, 'adapter': args.adapter, 'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
