# サイト別adapter

RSS/Atomで読めないサイトを、利用者ごとのPythonコードで取得する場所です。
別リポジトリは不要です。対象URL・抽出したい見出しや日付・実際のHTML例をAIへ渡し、
この契約に沿って自分のcheckoutへ実装・テストしてもらいます。

## 配置と登録

`my_site.py` のような個別モジュールをこのディレクトリへ置き、`local.py` で登録します。

```python
from . import my_site

ADAPTERS = {'my-site': my_site}
```

既存の登録がある場合は残して追加します。共通の `__init__.py` にサイト名を埋め込みません。
`local.py` がなければ個別adapterなしで起動し、通常のRSS/Podcast/YouTubeは使えます。
登録コード自体のエラーは起動時に通知されます。

## モジュールの契約

- `NAME`: 設定画面で使う表示名。
- `URLS`: 対応する取得元ページURLのタプル。指定されたURLをこの一覧と照合します。
- `parse(data: bytes, url: str) -> list[dict]`: 取得済みHTMLだけを解析します。通信は共通処理が担当し、JavaScriptは実行しません。
- 結果は共通の `html.entry(title, href, source_url, published=None, summary='', identity=None)` で作れます。見出し・URL・日時・抜粋を渡し、日時不明は `None`。同じURLに複数の更新記録があるときだけ、安定した `identity` を指定します。

構造変更で記事が見つからなければ、空の成功として黙らせず取得エラーを確認します。
生成したPythonは信頼して実行するコードなので、内容とテストをレビューしてから登録します。

## 検証

repoルートから、まず保存したHTMLで単体テストを実行します。ローカルのテストは `tests/local/` へ置けます。

```sh
uv run python -m unittest discover -s tests/local -v
uv run python scripts/check-source.py --adapter my-site --url https://example.com/articles
```

2つ目は実際に公開ページを取得する読取検証で、配信元登録は行いません。
稼働checkoutへ反映・再起動した後、「設定」の取得方法から選んで配信元を登録します。

## Git・公開版・更新時の保持

共通の `__init__.py`・`html.py` とこのREADMEだけをGit・公開版へ含めます。
`local.py`、サイト別Python、`tests/local/` はGit管理外です。公開スクリプトも個別コードを除外します。
同じcheckoutを更新するときも、別checkoutへ移すときも、事前に個別ファイルの所在を確認して保持してください。
Gitや公開版から個別ファイルを復元することはできません。

詳しいデータ形式は [設計](../../docs/design.md#サイト別取得)、画面操作は [使い方](../../docs/usage.md) を参照してください。
