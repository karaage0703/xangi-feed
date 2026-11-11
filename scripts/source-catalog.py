"""Export shared sources, or run an existing skill with that same catalog."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xangi_feed.catalog import export


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--consumer', required=True, choices=['feed', 'patrol', 'podcast', 'technews'])
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--run-script', type=Path, help='既存patrol/podcastスクリプトを共通リストで実行')
    args, remainder = parser.parse_known_args()
    sources = export(args.consumer, args.catalog)
    if not args.run_script:
        if remainder:
            parser.error('不明な引数です')
        print(json.dumps(sources, ensure_ascii=False, indent=2))
        return
    if args.consumer not in {'patrol', 'podcast'}:
        parser.error('--run-scriptはpatrol/podcastに対応します')
    spec = importlib.util.spec_from_file_location('shared_source_skill', args.run_script.resolve())
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if args.consumer == 'patrol':
        module.load_sites = lambda category=None: [s for s in sources['sites'] if not category or s.get('category') == category]
    else:
        module.PODCASTS = sources
    sys.argv = [str(args.run_script), *remainder]
    module.main()


if __name__ == '__main__':
    main()
