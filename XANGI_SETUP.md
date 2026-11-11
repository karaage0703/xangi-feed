# xangi-feed セットアップ

RSS・Podcast・YouTubeを読み、必要に応じてAI解説・xangiとの議論・Notion保存を使う拡張です。

## 必要なもの

- Web Chatとmanaged-http拡張に対応するxangi。
- Python 3.11以上とuv。実行時のPython追加パッケージはありません。
- 公開フィードとYouTubeへ接続できるネットワーク。
- 開発用のチェックを実行する場合はNode.jsとGit。

## 1. 依存を準備する

このリポジトリをcloneしたディレクトリで実行します。

```sh
uv sync --frozen
```

起動用の `scripts/xangi-extension` は、このディレクトリの `.venv/bin/python` を使います。チェックも行う場合は、依存準備後に `./scripts/check` を実行します。

## 2. おすすめフィードから始める

最初は [catalog.sample.json](catalog.sample.json) のブログ・Podcast・YouTubeの3件から始めるのがおすすめです。`.env.sample` のようにコピーして、必要な配信元を残したり追加したりできます。

```sh
cp catalog.sample.json catalog.local.json
```

初回起動の前に、**xangi本体の起動環境**の `XANGI_FEED_CATALOG` に、コピー先 `catalog.local.json` の絶対パスを設定します。`catalog.local.json` はGit管理対象外です。既に起動中のxangiへ、別のシェルから変数を設定しても引き継がれません。対象instanceの起動環境へ設定したうえで、拡張を起動してください。

このカタログを指定すると、初回は3件を登録して取得します。指定しなければ同梱の `catalog.json` を使います。既存環境でカタログを切り替えると、以前のカタログに含まれ、新しいものにない配信元は取得停止になります。記事データは残ります。

登録される項目を確認するだけなら、次を実行できます。このコマンドは登録・取得を行いません。

```sh
uv run python scripts/source-catalog.py --consumer feed --catalog catalog.local.json
```

`schema_version` は1、各配信元は一意の `id`・`name`・`url`・`kind`・`adapter`・`consumers` を持ちます。`consumers` に `feed` を含め、RSS / Atomでは `adapter` は `rss` にします。YouTubeは `kind` を `video` とし、サンプルと同じ公開フィードURLを使います。

画面から1件ずつ試す場合は、設定の配信元一覧で既存登録を確認してから、サンプルの値を「配信元を追加」へ入力します。YouTubeはチャンネルURL・ID・@ハンドルでも入力できます。

起動時と30分ごとに取得します。画面の取得状況を確認し、必要なら「今すぐ取得」を押します。「すべて」で取得済みの記事を確認し、分類・検索の条件が残っている場合は「すべて解除」で全件へ戻せます。記事がなければ「設定」の配信元一覧で有効／停止状態・取得エラーを確認してください。

## 3. xangiに登録して起動する

利用中のxangi instanceのCLIで拡張機能のhelpを確認し、次の順で操作します。別のinstanceを操作しないよう、対象CLIの絶対パスを使ってください。

1. `extension link` に、このリポジトリの `xangi-extension.json` の絶対パスを渡して登録する。
2. `extension start` の対象IDに `xangi-feed` を指定して起動する。
3. `extension doctor` の対象IDに `xangi-feed` を指定し、起動・health・workspaceが正常であることを確認する。
4. 対象instanceのWeb Chatで拡張一覧から **xangi-feed** を開く。

この拡張はxangiが認証情報とworkspaceを渡して管理します。エントリポイントを直接二重起動したり、別の定期取得cronを追加したりする必要はありません。更新時の依存準備はmanifestの `update.prepare` から実行されます。

## 4. 任意の連携を設定する

- **AI要約・コメント**: 「設定」→「日本語の要点・AIコメント」で、登録済みのエージェント・自動解説・件数を確認します。有効時は記事の抜粋や要旨と、xangi-searchから取得した関連記録の抜粋を、選択したAIへ送ります。生成したコメントは吹き出しに表示し、参照した記録を確認できます。関連記録が取得できない場合は記事内容に基づくコメントであることを明示します。音声・動画本編は解析しません。AI処理を使わない場合は自動解説を停止できます。
- **xangiと議論**: 記事の「…」から「xangiと話す」を選びます。ホストから接続情報が渡される環境では、接続先を手入力する必要はありません。
- **Notion保存**: 「設定」→「AIとスキルによるNotion保存」で担当AIとスキル名を指定します。同梱の `skills/xangi-feed-save/SKILL.md` を利用者のworkspaceまたはrepoの `skills-local/` へコピーし、保存先・保存対象・形式を編集します。Notion接続は担当AIが使うツール側で準備します。[使い方](docs/usage.md)を参照してください。保存依頼とAIの保存完了報告は区別して表示します。
- **iPhone／iPadのホーム画面版**: 「Safariアプリで開く」は初期OFFです。必要な端末でだけONにします。明示保存済みのON／OFFは維持されます。

## 保存場所と停止

記事・配信元・評価・設定・メモは、対象workspaceの `.xangi-feed/feed.sqlite3` に保存します。バックアップする場合はSQLiteのbackup機能など、稼働中DBを安全にコピーできる方法を使ってください。

分類・検索・サイドバー幅・Safari起動の選択はブラウザ側に保存され、端末間では同期しません。

停止・再起動は対象instanceの拡張管理から **xangi-feedだけ** を操作します。停止しても保存済みの記事は残ります。詳しい操作は[使い方](docs/usage.md)、データとAPIは[設計](docs/design.md)を参照してください。
