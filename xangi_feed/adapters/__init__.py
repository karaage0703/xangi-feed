"""Only reviewed, bundled Python modules can be selected by API clients."""
from ..feeds import web_url

# This local registry and its site modules are deliberately not tracked.
# AI edits these files in each user's checkout; no separate repository is needed.
try:
    from .local import ADAPTERS
except ModuleNotFoundError as error:
    if error.name != __name__ + '.local':
        raise
    ADAPTERS = {}


def validate(adapter, url):
    if not isinstance(adapter, str) or adapter != 'rss' and adapter not in ADAPTERS:
        raise ValueError('登録済みの取得方法を選んでください')
    if adapter != 'rss' and web_url(url) not in {web_url(u) for u in ADAPTERS[adapter].URLS}:
        raise ValueError('この取得方法に対応するページURLを指定してください')


def options():
    return [{'id': 'rss', 'name': 'RSS / Atom', 'urls': []}] + [
        {'id': key, 'name': module.NAME, 'urls': list(module.URLS)} for key, module in ADAPTERS.items()]


def parse_page(adapter, data, url):
    validate(adapter, url)
    result = ADAPTERS[adapter].parse(data, url)
    if not result:
        raise ValueError('ページから記事を抽出できませんでした（サイト構造変更の可能性）')
    return result
