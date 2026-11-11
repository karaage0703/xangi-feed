# 設計とAPI

拡張を開発・保守する人向けの資料です。画面操作は[使い方](usage.md)、導入は[セットアップ](../XANGI_SETUP.md)を参照してください。

```text
browser / agent → xangi同一origin proxy → xangi-feed
                                      ├→ 公開RSS/Atom・対応するWebページ
                                      ├→ workspace/.xangi-feed/feed.sqlite3
                                      └→ xangi Web Chat API（自動の日本語解説・本人操作の議論）
```

manifest schema 2 / managed-http / capability `xangi-feed.manage`。
UIの入口は `/entry`。ホストの `/ui` によるbase挿入・CSPの下で、固定スクリプトが通常はservice root、ブックマークレットのfragmentがある場合は確認用capture画面へ遷移する。外部記事からは正規の `/ui` を経由し、service proxyのcross-site拒否を維持する。単体の `/entry` では固定スクリプトのSHA256だけをCSPで許可する。
実画面と外部JS/CSSをservice経路で配信する。APIの基準URLはdocument.baseURIから解決する。
OSの自動割当localhost port、親が渡すBearer tokenで全経路を認証する。
初期化完了後にready eventを返し、収集はbackground threadで行う。

## 収集と状態

起動時・30分間隔・手動で取得。同時収集は1回に制限。各sourceの成功時刻、試行時刻、件数、エラーを保存する。
6配信元まで並列取得し、失敗しても既存記事を消さない。空フィードも診断対象。
1フィード8MiB・2000件上限、保存は論文以外100件まで。15秒socket timeout、redirect最大3回。
公開http(s)・80/443のみ。各redirectで全DNS候補の公開アドレスを確認し、接続IPを固定する。
実際のDTD/ENTITY宣言は拒否するが、CDATAに含まれるHTMLのDOCTYPE見本は本文として扱う。
HTMLはtextへ変換し、スクリプトや外部画像を表示しない。日本語を含むURLも符号化する。

canonical URLのhashを記事IDとする。追跡queryを削除、arXivはabs/pdf/revisionを正規化。
元URLの別名の意味的な同一判定は行わない。sourceとの多対多関連を保持する。
改訂時はタイトル・抜粋・話題を更新し、初回取得時刻・評価・保存・会話IDを維持する。
初期の並び順は更新日、新しい更新日がなければ公開日、それも不明なら初回取得時刻の降順。全日時はUTC timestamp。
RSSのpubDate改訂は初回の公開日を保持して更新日へ反映し、古いフィードを再取得しても既知の更新日を巻き戻さない。

SQLite transactionで保存。workspaceごとに分離し、同じworkspaceの複数process運用は対象外。
標準配信元は共通カタログから読み、RSS74サイト・Podcast20番組・arXiv2分野・ページ3件・YouTube1件の計100配信元を登録する。stand.fm専用番組1件はアプリ対象外。
カタログ同期はURLで配信元を追加・更新する。停止指定と記事・評価・保存・メモ・解説・会話は保持し、管理対象から外したURLは取得停止にする。
サイト別Pythonモジュールと共通の公開HTTP取得を使う。[サイト別取得](design.md#サイト別取得) を参照。

## 評価


1記事にrating=-1/0/1、saved、read、Bad reasonを保存。0は未評価。
`duplicate` と `summary` のBadは集計件数に含むが話題weightには含めない。
`interest` または理由未指定のBadは話題weightを-1、Goodは+1。
カスタムは記事の新しさとGood／Badのtopic weight（各-3〜3）のtanhで計算。無反応・既読・議論は学習しない。
リセットはratingとreasonだけを消し、保存・既読・会話を維持する。
新着取得は画面を勝手に並べ替えず、更新ボタンで適用する。
カスタムは既読と配信元関連のなくなった履歴記事を除外する。読了・すべて・保存・議論では表示し、read=falseで戻せる。ミュート済みは各通常一覧から除き、ミュート一覧で確認する。
既読操作は明示ボタンに加え、mark_read_on_openがONなら原文リンク操作時にも行う（既定ON）。詳細を表示するだけではreadを変更しない。保存済みのreadフラグをそのまま読了状態として使う。自動解説の候補はこの非表示条件から独立。
並び順は全一覧で updated（既定）/ published / preference / recommended を指定できる。
preferenceは話題weight合計と記事のratingの和を降順にする。重複・要約のBadは減点しない。
同順位は更新日・初回取得日・IDで安定させる。選択はブラウザのlocalStorageに保存する。
カスタム一覧のrecommended順だけ、ページ分割前にテック・ブログ・Podcast・論文等の種類を交互にする。

### カスタムの表示条件

設定はSQLiteのmetadataに保存し、カスタムだけに適用する。設定値と既定値は以下のとおり。

- `recommendation_min_preference`: 既定null（指定なし）。指定時はその点数以上の記事を表示する。
- `recommendation_days`: 0〜365日、既定0（全期間）。更新日、不明なら公開日・初回取得日で判定。
- `recommendation_hide_bad`: 既定false。trueなら本人がBadを付けた記事を候補から外す。
- `recommendation_priority`: balanced / fresh / interest。既定balanced。
- `recommendation_mix_types`: 既定true。カスタム一覧のrecommended順のみ、種類内の順位を作ってから交互に出す。falseなら全体のscore順。

scoreは `新しさ係数/(1+経過時間/48h) + 好み係数*tanh(話題weight合計/3) - 興味なしBadなら0.6`。
係数はbalanced=(1,0.7)、fresh=(2,0.35)、interest=(0.5,1.4)。
公開日が未来の場合は経過0、話題weightは各-3〜3へ制限する。
種類または配信元を絞り込んでいる場合は交互表示をせず、選択した並び順で表示する。
0件のときは条件の見直しと「すべてを見る」を案内する。すべて・保存・議論にはこれらの条件を適用しない。
推薦条件は要約の入力hashに含めない。自動要約の候補選定は表示条件から独立する。

## 日本語の要点

`Enricher` は登録済みAIに1記事ずつ渡し、`title_ja`・`summary_ja`・`comment`・`evidence_ids`を生成する。タイトル・取得本文/抜粋・種類と、xangi-searchの関連資料（最大3件、抜粋各600文字）が入力になる。Good／Bad・メモは渡さない。

- local-llmは選択モデルへ直接JSON生成を要求する。出力上限2048トークン、thinking無効。その他の担当は専用automation sessionとchat SSEを使い、終了処理の失敗で生成結果や元の例外を上書きしない。
- 待ち時間は既定300秒（1〜1800秒）、待ち行列200件。取得後は直近14日から既定30件（0〜100件）、一覧表示時は先頭50件を候補にする。
- 有効な記事ID・日本語の見出し/要点/コメント・参照IDを検証する。参照は渡した資料から最大3件で、未知・重複・他記事のIDを拒否する。
- 入力hashが同じ解説を再利用する。Good／Badや表示条件の変更では失効させず、本文の変更中に生成した古い結果は採用しない。
- 再生成中・失敗時は、同じ入力に対する前回の要約とコメントを保持する。エラーは15分後の一覧取得で再試行でき、起動時にも再試行可能な直近エラーを最大200件復元する。
- 再起動時のpendingは復元し、processingは結果不明のerrorにする。自動解説停止中は新たにAIへ送らない。件数0でも一覧表示による追加は有効。

## 根拠付きAIコメント

生成時の検索状態と、AIが実際に引用した記録のID・path・抜粋をcomment_contextへ保存する。AI応答から出典pathを採用せず、サーバー側の検索結果だけを対応付ける。コメントと出典は同じanalysis結果へ保存する。search失敗は要約・コメント生成を止めず、空の根拠として明示する。

一覧と詳細に「AIコメント」と書いた吹き出しを表示し、要約と区別する。「コメント生成時に参照した記録」で固定した当時の抜粋を開ける。後から行う手動検索の結果とは別に保持する。根拠がない/未取得/未参照のコメントは記事内容のみが材料であることを表示する。

吹き出しの生成・作り直し操作は既存analyze APIを使い、根拠を再検索して要約とコメントを生成する。searchの更新だけでは古いコメントを書き換えない。更新中・更新失敗は前回の内容に状態を添える。コメントや根拠は好みの点数・並び順へ加算しない。保存を依頼した場合は生成済みAIコメントと参照pathも保存用snapshotに含める。

## 好み・配信元の表示

Store.itemとlist_itemsの両方でpreference_scoreとpreference_knownを返す。同じ計算を使い、既知の0と評価なしを区別する。rating/reason更新の応答にはpreference_weightsを含め、ブラウザは表示中の記事の点数を更新する。表示順は変えない。

itemsへpublication_name / authorを空文字既定で追加する。RSS／Atomのchannel/feed titleとauthor/creator、entry source、HTMLのog:site_nameとauthor、YouTube videoDetails.authorを保存する。取得失敗や情報欠落で既存値を空にしない。一般的なサービス名で固有名を上書きしない。

publisher_labelは固有の配信元名・著者・URLのドメインを組み合わせる。note.com/<account>/n、qiita.com/<account>/items、zenn.dev/<account>/articles等ではアカウント部分をURLから表示する。名前が不明な場合はアドレスを表示し、著者名を推測しない。
配信元は日付より大きく太字・本文色で表示し、記事タイトルより控えめにする。長い名称やドメインは折り返す。

HTMLのタイトル末尾は、別途取得した記事タイトルと一致する先頭部分の後に明示的な区切りがある場合だけサイト名として使う。著者プロフィールURLを人名として表示しない。本文・要約・利用者の評価・メモ・保存状態はmetadata補完時にも保持する。

## 推薦根拠（xangi-search）

記事詳細の「推薦根拠を探す」から `GET /api/items/{id}/evidence` を呼ぶ。
HostClientの既存認証付きproxyで `/api/extensions/xangi-search/service/search` にタイトルをqとして渡す。
mode=hybrid、k=3、s=0.3、forgetting=false、timeout=10秒、応答上限256KiB。
結果のfile_pathとcontentだけを採用し、最大3資料、抜粋各1200文字まで。検索scoreを好みの点数へ混ぜない。
ready / empty / unavailable / errorを区別し、degraded時はキーワード検索への切替を表示する。
空・失敗を低関心扱いせず、Good/Bad・保存・読了・順位を変更しない。根拠の文章はAIで作らない。
取得はボタン操作時だけで、一覧表示による記事数分の検索はしない。ブラウザ内に最後の結果を保持し、再検索可能。
根拠は検索で見つかった関連資料であり、本人がその記事を好む証明や実際の順位計算の内訳ではない。

## 議論

Web会話連携には `/api/sessions` と `/api/chat` を使う。
記事ごとのlockではなく、作成処理全体を単一lockで守り、同時操作を拒否する。
ダイアログで追記（最大4000文字）と任意の登録済みagentを指定する。新規sessionはprojectId=nullで作る。
session作成→ID・担当保存→文脈送信の順。作成済みで追記が空なら同じ会話を開くだけで送信しない。
追記は既存会話へ送るが、処理中・終了済みなら拒否する。別担当への相談は明示的に新しい会話を選び、記事の参照先を新sessionへ変更する。
終了済み会話も開くことはできる。新規会話にプロジェクトを指定しないが、親xangiのプロジェクト表示状態は変更しない。
クリック中に別タブを先に確保し、ブロックされた場合はAPIを呼ばない。親のニュース画面を残す。ブラウザによっては別タブになる。
送信内容はタイトル・URL・フィードまたはページの抜粋と未確認範囲。外部内容を命令として扱わない注意を付ける。
ホストへのHTTP redirectは追わず、tokenを別originへ送らない。
作成・送信がtimeoutした場合は `uncertain`。自動再送せず会話一覧または保存済み会話で確認する。
追記・担当指定・新規会話にはUUIDのrequest_idが必須。同じIDの再試行は保存済み結果を返し、内容変更を拒否する。
discussion_requestsに送信前のjournalを保存する。起動時もpendingとcreating/sendingをuncertainへ移し、結果不明の操作を再送しない。送信受付はAI完了とは別。

## API

以下はすべて実行時Bearer必須。通常はホストのextension_requestまたは同一origin UIを使う。
更新はJSONのみ、最大16KiB。APIは認証情報を返さない。

- `GET /api/health`: version、ready、collector、discussion_available。
- `GET /api/items?tab=recommended|all|manual|saved|notes|read|discussed|muted&sort=updated|published|preference|recommended&kind=tech|blog|news|comic|rss|podcast|paper|video&q=&source=&limit=50&offset=0`: 一覧。sortは既定updated、最大100件/ページ。
- `GET /api/items/{id}`: 本文抜粋、評価、配信元、会話状態。
- `GET /api/items/{id}/evidence`: xangi-searchの関連資料と取得状態。
- `POST /api/items/{id}/react`: rating、saved、read、reason、mutedの部分更新。
- `GET /api/discussion-options`: 登録agentの候補と既定担当、project_id=null。
- `POST /api/items/{id}/discuss`: prompt、agent_id、new_conversation、request_idを受け、会話を作成・追記、または既存会話IDを返す。追記なしの場合は会話を作成または再開する。
- `POST /api/items/{id}/analyze`: 日本語の見出し・要点を非同期生成/再生成。
- `GET /api/settings`: AI担当・候補・自動要約の設定。
- `POST /api/settings`: enabled、agent_id、automatic_limit、timeout_secondsと上記recommendation_*の部分更新。推薦条件だけの更新ではAI待ち行列を復元しない。
- `GET /api/sources`: 配信元・状態。
- `POST /api/sources`: name、url、kindで追加。
- `POST /api/sources/{id}`: enabled=true/false。
- `POST /api/refresh`: 非同期収集、HTTP 202。完了はhealthで確認。
- `GET /api/profile`: Good/Bad/保存件数、話題weight。
- `POST /api/profile/reset`: confirm=true必須。

UIは5秒ごとにhealthのみpollし、取得完了時に新着ボタンを出す。
production xangiへの登録・起動や実モデルへの送信は、ローカル検査とは別の導入確認とする。

議論はwindow.openにpopup・サイズ指定を渡さず通常の新規タブを確保してから送信する。二重送信防止とブロック時の非送信は維持する。

読了はtab=readで既読だけをページ分割前に選ぶ。カスタムの表示条件は適用せず、選択中の並び順・検索・種類/配信元での絞り込みとページ追記を使う。
読了タブを押すと読了タブの分類条件を復元する。未読に戻した行は読了から外し、その他のカスタム条件に合う場合は再表示される。read列を使って読了状態を保存する。

操作・YouTubeの登録は [利用ガイド](usage.md) を参照。共通カタログの契約は本書に記載する。


## メモとNotion保存

`items.note`（最大4000文字）と `note_updated` に保存する。再取得・評価・読了・配信元停止でメモを変更しない。検索にメモを含め、`tab=notes` でメモのある記事を選ぶ。メモを自動解説の入力や好みの重みに含めない。UIの下書きは画面内で保持し、保存成功後に解放する。HTMLとして解釈しない。

`skill_saves.py` が明示的な保存依頼を、選んだAIの通常会話と信頼したローカルスキルへ渡す。`HostClient.create(agent_id=...)` はautomationを指定せず、Notionなど利用者のツールを使える会話を作る。記事内の命令と保存スキルを区別する。保存対象・保存先・形式はスキルが決める。アプリはNotionクライアントや固定のメディア保存処理を起動しない。

`save_requests` はUUID・記事ID・会話ID・担当・スキル名・状態・結果URL・説明を永続化する。同じUUIDの再試行は同じ結果を返し、別記事への流用は拒否。記事にcreating/sending/requested/needs_input/uncertainがあれば新しい依頼を作らない。会話IDを送信前に記録し、通信結果不明はuncertainとして自動再送を止める。起動時もcreating/sendingはuncertainへ移す。requestedはAIの処理中として保持する。

AIはスキルを読み、利用者の接続済みNotionツールで保存・再取得照合する。結果のPOSTは記事・UUID・会話IDを照合し、saved/partialには実際のHTTPS NotionページURLを必須とする。API認証は共通の拡張認証を使う。アプリ表示はAIの自己報告であり、Notion本文の独自再照合はしない。結果はhealthのsave_revisionでUIへ知らせ、会話へのリンクと一緒に表示する。依頼受付だけで保存完了と表示しない。

設定は `save_agent_id`（空ならxangi既定）と `save_skill_name`（初期xangi-feed-save）。保存AIは解説AIの設定と独立。スキル探索は対象workspaceのskills、repoのskills-local、初期名の場合のみ同梱サンプルの順。名前をslugに制限し、記事からパス・命令を受け付けない。個別スキルはGitと公開対象から除外する。


- `POST /api/items/{id}/note`: `{"note":"..."}`。空文字で消去。
- `GET /api/items?tab=notes&q=...`: メモのある記事とメモ検索。
- `POST /api/items/{id}/save`: `{"confirm":true,"request_id":"UUID"}`。AIへの依頼状態を返す。
- `POST /api/items/{id}/save-result`: request_id/session_id/status/page_url/summary/error。statusはsaved/partial/needs_input/error。
- `GET /api/items/{id}` と一覧: `save_request` を含む。
- `GET /api/settings`: `save_mode:skill`、save_skill_name、save_agent_id、save_available、登録済みagents。
- `POST /api/settings`: 保存担当とスキル名を保存する。

## サイト別取得

サイトごとのPythonモジュールを `xangi_feed/adapters/` に追加し、同じ場所の `local.py` に `ADAPTERS = {取得方法ID: モジュール}` を定義する。`local.py` と個別モジュールはGit追跡対象外。共通の `__init__.py`・`html.py` と [adapter README](../xangi_feed/adapters/README.md) は追跡・公開する。別repoやプラグイン配布は不要で、各利用者がAIに自分のcheckoutを直接編集してもらう。xangiへは対象URL・抽出したい記事・日付の扱いを伝え、この契約に沿ったスクリプトとテストの作成を依頼する。

モジュールの契約:

- `NAME`: 取得方法の表示名。
- `URLS`: 対応するページURLのタプル。登録時にこの一覧と照合する。
- `parse(data: bytes, url: str) -> list[dict]`: 渡されたHTMLだけを解析する関数。通信は共通の取得処理が担当する。外部HTML内のJavaScriptや命令は実行しない。
- 戻り値は `.html.entry(...)` で作る。見出し・URL・公開日・抜粋を渡し、日付不明なら `None`。同じURLに複数の更新記録があるときだけ、安定した `identity` を指定する。通常記事の同一URLはRSSとの間でも重複排除される。

利用できる処理はローカルで確認して登録したモジュールだけ。画面から任意のファイルパス・コマンド・Pythonコードは指定できない。追加後はコードレビュー・テスト・許可された更新と再起動を経て取得方法に現れる。

`local.py` がなければ登録は空になりRSSはそのまま使える。登録ファイルの内部エラーは隠さず起動失敗とする。個別実装は信頼したPythonコードとして動くため、AIの生成後に内容とテストを確認する。共通HTML処理は標準ライブラリのみ。

AIへの依頼例: 「このURLから見出し・リンク・公開日を抽出するadapterとテストを作り、`local.py` へ登録してください。抽出時に通信せず、日時不明はNone、サイト構造が変わった場合は失敗にしてください」。個別テストは `tests/local/`（Git追跡対象外）に置き、`uv run python -m unittest discover -s tests/local -v` で実行する。単独取得は `scripts/check-source.py --adapter <ID> --url <URL>` を使う。


### 保存

`sources.adapter` は既定 `rss`。RSSの記事は `content_origin=rss`、ページ取得は `page` を保存する。画面とAI入力で情報源を区別する。記事IDを一意とし、同じページの複数更新記録はidentityで識別する。

### API

- `GET /api/source-adapters`: ローカルに登録済みの取得方法と対応URL。
- `POST /api/sources`: `name`・`url`・`kind` と任意の `adapter` を受け付ける。省略は `rss`。
- `GET /api/sources`: 各配信元の `adapter` と取得結果も返す。

## 共通カタログ

正本は `catalog.json`。アプリの標準配信元、patrol、Podcast、technewsは同じ定義を使い、`consumers` で利用先を選ぶ。Podcastの番組ID・listen URL、patrolのページ用selectorや文字コードも同じ行に保存する。開発用カタログと公開用サンプルは別に管理する。公開時の `catalog.json` は `catalog.sample.json` の内容を使用し、サイト固有の配信元やselectorを持ち込まない。

共通にするのは配信元の定義。アプリの既読・評価・有効/停止と、Podcastスキルの配信済み台帳は別々に保持する。
アプリ画面で追加する個人用配信元はDBに保存される。スキルにも共有したい追加・修正は `catalog.json` を編集する。アプリは次回収集時にカタログを再読込し、既存の停止設定を維持して追加する。カタログから外した管理対象は取得停止にし、記事を消さない。


## 既存スクリプトを共通リストで動かす

既存スキル自身のファイルを複製せず、読込後に配信元の定義だけを渡すラッパーを用意している。

```sh
# このリポジトリのディレクトリから実行する
uv run --with feedparser,beautifulsoup4,pyyaml,requests python scripts/source-catalog.py \
  --consumer patrol --run-script /absolute/path/to/xs-site-patrol/scripts/fetch_sites.py --json

uv run --with feedparser python scripts/source-catalog.py \
  --consumer podcast --run-script /absolute/path/to/xs-podcast-checker/scripts/podcast_checker.py check --json
```

既存スキルが必要とする依存と引数・実行手順はそのまま使う。上の2コマンドは実際に取得を行い、Podcastは元のスキルのpending台帳を使うので、通常のスキルの記録・確定手順に従う。既存のcommitコマンドをこの開発作業で実行する必要はない。

technewsはまず `--consumer technews` の一覧を読み、その各RSSから記事を選ぶ。無料記事の条件や、注目記事の選び方は既存スキルのままにする。


## 画面方針

ニュースを読む個人向け。新着を見つけ、評価し、気になった記事をxangiへ持ち込む。

- 見出し・本文を中心にした1列。記事は罫線で区切り、読み幅は最大900px。
- 明るい背景と青の操作色。OS設定に応じた暗色も用意。外部フォントに依存せず、画像の取得失敗時も文字で操作できる。
- スマホも同じ記事順・操作名。詳細と設定は閉じて一覧へ戻れるダイアログ。
- 新着取得で読書位置を動かさない。追加分はボタンで適用する。
- Good / Bad / ブックマーク は独立。押下状態・フォーカスを色以外にもラベルと枠で表現。
- 抜粋の情報源と未確認範囲を詳細へ表示。未設定・空・取得中・失敗を表示する。
- 参照した品質指針: Hallmarkの可読性・状態・横幅、DADSのラベル・フォーカス・エラー。
  https://github.com/Nutlope/hallmark
  https://design.digital.go.jp/dads/

主要操作: 一覧のタイトル→原文を別タブ。「記事の詳細」→詳細→好き/興味なし・保存→閉じる→ブックマーク→議論→会話リンク。
並び替えは件数の隣で選択。読了は読了に追加した時刻、個別追加は最初に個別追加した時刻（manual_added_at、日時不明はfetchedで代用）の新しい順、それ以外は更新日が新しい順を初期値に、公開日・Good／Badによる好み・好みと新しさによる順を選べる。
記事詳細の「推薦根拠を探す」で関連資料と抜粋を表示する。
議論は追記と任意の担当を選ぶダイアログを経て別タブへ開き、ニュースの読書位置を残す。
既存会話の担当を変える場合は新しい会話にする。送信中は入力と閉じる操作を止め、二重送信を防ぐ。
上部の紹介コピー・日付帯は置かず、ヘッダーから表示切替とニュースへ進む。
設定: 配信元追加→取得→状態確認、配信元停止、評価集計→確認付きリセット。

「読んだ」「未読に戻す」は評価・保存と同じボタンの形にする。カスタムで読了にしたらその行だけを外し、キーボードのフォーカスを次の既読操作ボタンへ移す。
詳細を開くだけでは既読にしない。読了・すべて・保存・議論の「未読に戻す」で戻せる。
「読了」タブは読み終えた記事の行き先。「読んだ」を押すとここへ入り、「未読に戻す」でこの一覧から外れる。操作後に結果を短く表示する。

YouTubeでは視聴済み／未視聴のラベルを使う。リンクを開いたときの自動読了は`mark_read_on_open`に従う。

詳細の末尾にメモ欄・保存結果・Notionのリンクを置く。保存中は入力・保存操作を無効化し、成功／失敗をrole=statusで示す。「メモ」タブから振り返り、詳細を閉じて一覧へ戻る。PC・スマホで同じ操作名を使う。

記事一覧は読むことを中心にし、評価・ブックマーク・メモを小さなアイコン、Notion保存を短いラベル付きの主操作にする。詳細・読了・音声・会話はネイティブの「…」detailsメニューへまとめる。モバイルでも常用操作は1段に収める。保存ボタンは確認ダイアログや詳細画面への強制遷移なしでAIへ依頼し、その記事の横に依頼状態・AIの結果報告とリンクを示す。未保存のメモ下書きも保存後に送る。詳細内にはAI解説の再生成とメモ編集を残す。Good/Badの選択状態、代替ラベル、キーボードのフォーカス、メニューEscape操作を維持する。

参考: Feedlyの保存アイコン、Readwise Readerの補助操作メニュー。既存の配色を保ち、白い読書面・静かな罫線・抑えた要約量で情報の密度を整える。PC1280px・スマホ390px・360pxの操作フローを検証する。
https://docs.feedly.com/article/338-saving-an-article-to-your-read-later-section
https://docs.readwise.io/reader/docs/faqs/exporting

### 記事の個別追加


記事の個別追加は `POST /api/items` に `{url, title?, summary?, background?}` を送る。新規は201、既存記事は200で `{item, created}` を返す。手入力の評価・メモを保持し、公開HTMLやYouTube公開字幕から本文を補完する。詳細は末尾の直接追加の本文取得を参照。

### 配信元の削除

配信元の削除は `POST /api/sources/{id}/delete` に `{confirm: true}` を送る。認証・同一サイト制限は他の変更APIと共通。SQLiteの `sources.deleted` に削除状態を保持し、有効な配信元一覧から外す。収集中に削除された配信元の取得結果は取り込まない。記事と配信元の関連・評価・メモ・会話・Notion情報は保持。カタログ同期は削除状態を解除せず、同じURLの明示的な再追加で登録を復元する。


## 分類で絞り込む

PCは左の分類パネル、900px以下は同じコントロールをネイティブdialogへ移動して表示する。xangi-tasksの常設サイドバーとモバイル開閉の操作を参考に、既存の色・記事表示を維持する。種類は1件、話題タグは複数選択でOR、種類・配信元・キーワードとタグの各条件はAND。自動話題タグと自分のタグを別グループで選べる。

`GET /api/filter-options` は既存の話題タグ候補を返す。`GET /api/items` は繰返しの `topic` queryを受け付け、件数算出・ページング前にタグを絞り込む。候補外のタグや上限超過は400。未選択時はタグなしの記事も表示する。配信元の検索は名前だけで行い、URLや秘密トークンを検索候補へ出さない。

確認用: `uv run --offline --with playwright python tests/classification_browser.py /absolute/path/to/evidence`。750配信元のfixtureを使い、PC・スマホ・狭いスマホで分類、タグ複数選択、配信元検索、解除、記事タグからの絞り込み、Escと幅変更を検証する。


## 手動タグ

manual_tags(name)とitem_tags(item_id, tag_name)の追加テーブルで管理し、元の記事・自動topicsを変更しない。DB初期化時に既存DBへテーブルを追加する。タグ名はNFC正規化・前後空白除去、1〜40文字、制御文字不可。1記事20個。追加・取り外しはトランザクション内で行い、同時に別のタグを操作しても互いを上書きしない。

`POST /api/items/{id}/tags` に `{add: 名前}` または `{remove: 名前}` を送る。追加はタグを再利用・新規作成して記事へ関連付ける。取り外しは当該記事の関連だけを消す。`POST /api/tags/delete` は `{name, confirm: true}` でタグ本体を削除し、外部キーのCASCADEで全記事との関連を解除。記事テーブルの行は削除しない。

`GET /api/filter-options` のtagsは名前・利用記事数を返す。`GET /api/items` の繰返しtag queryは手動タグ内のOR、自動topics等の他条件とはAND。件数・ページング前に適用する。手動タグはAI入力ハッシュ・評価の話題集計には含めない。保存AIへ渡す文脈に手動タグを含め、Notionでの表現は利用者の保存スキルが決める。

操作検証: `uv run --offline --with playwright python tests/manual_tags_browser.py /absolute/path/to/evidence`。fixtureのみを使用し、PC/393px/360pxで新規追加・既存名の再利用・再読込・記事から取り外し・絞り込み・削除キャンセル・全記事から削除・記事とメモの保持を確認。


## カスタム条件とPWAの記事リンク

xangi-tasksの一覧内オプション開閉を参考に、優先する要素をカスタムのトップに表示し、期間等の条件をdetailsへ格納する。フォームは単一とし設定ダイアログ側に複製しない。保存には既存POST /api/settingsを使用し、変更時に自動保存・再表示する。保存後も現在のtabを維持する。GET /api/recommendationsは既存のカスタム設定だけを返し、エージェント列挙やNotion設定の読み取りを伴わない。初回読み込み成功までfieldsetを無効化し、未取得の初期値で保存しない。

記事一覧と詳細の原文は通常anchor（target=_blank、rel=noopener noreferrer）を基本とし、後述のiPhone PWAのSafariアプリ起動モードではOSハンドラのURIへ切り替える。クリックをpreventDefaultせず、タップでブラウザが直接別画面を開く。元のフィードは遷移しない。共有・コピーdialogは記事のメニューから明示的に開く補助機能で、主操作に挟まない。

仕様参照: [WebKit User Activation API](https://webkit.org/blog/13862/the-user-activation-api/)、[MDN Window.open](https://developer.mozilla.org/en-US/docs/Web/API/Window/open)。実機のOS固有の挙動を解消済みとはせず、最初に申告されたPWAで開かない現象の原因は未確定。xangiホストの拡張はiframeではなく同じページへのlocation.assignで開かれ、サービスproxyは拡張のCSPを転送する（対象コードを確認）。今回の修正ではホスト設定を変更しない。 [Apple WWDC23の説明](https://developer.apple.com/videos/play/wwdc2023/10120/)ではiOSのHome Screen web appのスコープ外リンクがSafari View Controllerで開くとされるため、JavaScriptで遷移先を変えずnative linkをOSへ渡す方針とした。実機の表示形式は別途確認する。

検証: `uv run --offline --with playwright python tests/top_controls_pwa_browser.py /absolute/path/to/evidence`。トップからの条件適用と再読込保持、通常ブラウザの別タブ、standaloneを模した2系統の環境で、1タップの別画面、元フィードのURL保持、詳細から別画面へ開いた後も詳細を維持、明示的な共有・コピーの補助操作をfixtureで確認する。JavaScriptのwindow.openを禁止してもnative anchorで別画面が開くことを検証する。


## 原文リンクでの自動読了

mark_read_on_openをsettingsへ保存し、デフォルトtrue、保存値は厳密なboolean。既存設定にキーがなくてもtrueとして解釈し、記事の既存read値は一括変更しない。POST /api/items/{id}/openは空objectだけを受け付け、トランザクション内で現在の設定を見てreadを1にする。OFF/既読なら変更せず、itemとmarked_readを返す。手動reactは設定から独立。

記事/動画/音声のanchorにclick/auxclickを追加するが、preventDefaultやawaitでnativeの別画面を遅らせない。読了POSTはkeepalive付きfetchで並行する。タグ・詳細の表示・コピー/共有・Notionリンクは対象外。成功時に該当行・詳細の読了状態を更新し、カスタムからは除外。失敗時はリンク操作を取り消さず読了保存エラーを表示する。外部ページの表示成功そのものやOSコンテキストメニューは検知しない。

検証: `uv run --offline --with playwright python tests/read_on_open_browser.py /absolute/path/to/evidence`。fixtureのみでONの自動読了・OFF保存/再読込・明示読了・別画面維持・詳細のメモ下書き・音声・コピー非対象・保存失敗時のリンク維持をPC/393px/360pxで確認する。


## iPhone PWAから外部Safariへ渡す

利用者はiPhone/iPadホーム画面起動、既定ブラウザSafariと回答。単なるtarget=_blankではPWA外への離脱を保証しないため、HTTPSの原文/動画/音声リンクだけをx-safari-https:スキームへ変換してOSへ渡す。target=_selfでPWA内の余分な空ウィンドウを作らず、ネイティブanchorのユーザー操作に任せる。preventDefault、window.open、非同期後のredirectは使わない。

iOS判定はiPhone/iPad/iPodのUA、またはMacIntel＋複数touch pointsのiPadOS判定とstandaloneの組合せ。HTTPS以外と認証情報付きURLは変換しない。元のURLをdata-article-urlに保持し、共有/コピー/DBのURLは変換しない。読了のPOSTは既存keepalive処理を維持。

端末ローカルのxangi-feed-ios-external-safariでON/OFFを保存（既定OFF）。保存値がonの場合だけ有効にし、未設定・不明値・保存を読み取れない場合は無効にする。明示保存済みのON/OFFは保持する。iOS PWAでだけ設定sectionを表示し、OFF変更は表示中anchorへ即反映する。保存不可時はこの画面のみ適用と表示。サーバー設定・DB・manifest・依存は変更しない。

一次観察: [Safari URL Scheme for macOS and iOS](https://christiantietze.de/posts/2023/05/safari-for-mac-url-scheme/)。iOS15/17/18で動作、16では非動作との記載がある。Apple公開Web APIとして保証された機能とは扱わない。[MozillaのiOS報告](https://bugzilla.mozilla.org/show_bug.cgi?id=1986355)にもSafariハンドラへの遷移の観察がある。コードを転載せず、URI形式の仕様確認だけに使った。

検証: tests/external_safari_browser.pyでiPhone/iPad PWAのURI生成、通常iPhone/PC/Androidで非変換、設定OFF保持/ON復帰、原文URL保持、読了ON/OFFを確認。OSのプロトコルハンドラ部分は模擬受付であり、Safari実起動を検証したことにはしない。

## 読了履歴とカスタム条件の即時反映

items.read_at（nullable REAL）を追加。既存read=1の日時はNULLのまま保持し、推測で補完しない。未読→読了の遷移だけtime.time()をサーバーで保存し、既に読了の場合は保存時刻を変えない。未読化でread_atをNULLへ戻す。手動reactとopen_itemの双方を同一transaction内の判定で扱う。クライアント提供のread_atは書き込まない。

/api/itemsのsort省略時はreadがread_at、manualがmanual_added_at、それ以外はupdated。明示sortで並び順を指定できる。read_at順は時刻既知を優先し、新しい順、同値/不明は更新日・取得日・idで安定化。検索・絞り込み後、全体を並べてからページングする。

画面の一覧は「カスタム」「おすすめ」「すべて」「個別追加」「ブックマーク」「メモ」「読了」「議論した記事」「ミュート」。読了のソートはxangi-feed-sort-read、個別追加はxangi-feed-sort-manual、他の一覧はxangi-feed-sortへ保存する。初期値は読了がread_at、個別追加がmanual_added_at、それ以外がupdated。各一覧の選択を保持し、read_atは読了、manual_added_atは個別追加だけに表示する。

カスタム条件欄は当該tabだけ表示。recommendation_min_preferenceはnull（既定）または-100〜100の整数。数値指定時はpreference_knownが真でpreference_scoreが下限以上の行だけを件数計算・ページング前に選ぶ。並び順から独立し、all等の他一覧には適用しない。select/checkboxのchange、日数inputの300msデバウンスで自動保存。Promise直列処理と版番号で古い待機入力を省き、最後の入力を最後に保存する。完了時に別tabへ移動していれば一覧更新もtab変更も行わない。読み込み完了までdisabled、保存失敗時は入力を保ってエラー/再試行を表示。不正日数は保存しない。

デザインは既存の色・文字・余白・境界と分類パネルを維持。PC/393px/360pxで、カスタム変更→別一覧→読了→未読化→ブックマーク→詳細→戻るを確認する。DADSチェックボックスのラベル/位置/状態の原則を参考にした（https://design.digital.go.jp/dads/components/checkbox/usage/）。Feedly/InoreaderはRead Laterを用いるが、本製品ではGood/Badと区別し、読了後も保持する保存場所としてブックマークを採用。参考: https://docs.feedly.com/article/338-saving-an-article-to-your-read-later-section 、 https://www.inoreader.com/features/read-later 。

## 記事のミュート

ミュート状態を記事に保存する。POST /api/items/{id}/reactはmuted:booleanを受け付ける。itemsのAPI応答にはmuted:booleanを含める。

すべての通常一覧でミュート済みを件数・ページング前に除外し、tab=mutedではミュート済みだけを選ぶ。読了・ブックマーク・メモ・議論・個別追加の状態は保持。種類・タグ・検索・配信元による絞り込みはミュート一覧でも適用する。評価・好みの重み・Notion状態を変えない。新しい自動解説候補の一覧からもミュート済みを除外する。

一覧と詳細にミュート/解除の44px操作を常時置く。成功応答後に該当行を外し、失敗は行と状態を保持する。二重クリックは1回の更新に抑止。次の操作へフォーカスし、通常一覧の最後の行なら「ミュート」、ミュート一覧の最後の解除なら「すべて」へ移す。詳細のメモ下書きは維持する。解除後の表示先は読了・ブックマークなどの元の状態と現在の絞り込み条件に従う。

## 直接追加の本文取得

POST /api/items は入力値の検証・既存記事照合後、公開IP限定・リダイレクト再検査・接続先IP固定・8MiB上限の共通取得を使う。本文はcontent_text（最大50,000文字）、取得種別はcontent_kind（article/youtube_description/youtube_captions/youtube_transcript）、結果はfetch_state/fetch_errorに保持する。本文とタイトルを取得できた一般記事はkind=blog、YouTube動画はkind=video。手入力値・評価・メモ・タグ・読了は保持する。既存RSS記事を直接追加で上書きしない。

background:trueで追加すると本文取得は別スレッドで動き、GET /api/items/{id}のfetch_stateがfetchingの間だけ画面が状態を確認する。GETに副作用はない。既存のURLのみの記事も再追加またはPOST /api/items/{id}/fetch（background:true可）で補完できる。再起動で中断した取得はerrorとして再取得を案内する。本文をAI解説・議論・保存AIへの文脈へ渡し、本文変更時はAIキャッシュを更新する。YouTubeは公開字幕のみを取得し、音声の自動取得・文字起こしは行わない。取得HTMLのJavaScriptは実行しない。

## 個別追加の記事一覧

items.manually_added（INTEGER NOT NULL DEFAULT 0）で、個別追加した事実を本文のcontent_originやkindと分けて保持する。新規・既存どちらのPOST /api/itemsでも1にする。RSS再取得はこの列を更新しない。

GET /api/items?tab=manualはmanually_added=1を件数・ページングの前に選ぶ。カスタム条件・読了による除外はせず、ミュート済みは除外し、種類・検索・配信元・タグとのANDは維持する。新しい種類は作らず、ブログ・YouTubeの区別も保持する。上部の既存表示メニューへ「個別追加」を加え、一覧と詳細のメタ情報にも印を表示する。空状態から記事追加へ進める。

既存の配色・フォーカス・横スクロール可能な表示メニューを維持。PCとスマホで「個別追加→記事追加→詳細→戻る→種類/検索→読了→再表示」を確認する。参考: https://design.digital.go.jp/dads/components/tab/ （表示目的の切り替えと絞り込みを区別し、種類は既存の分類コントロールを使う）。


## おすすめ検索

公開Webの検索結果からRSS/Atomを検証し、新しい記事を最大6件返す。画面の使い方は[おすすめの記事](usage.md#おすすめの記事)を参照。

### 検索条件

| 入力 | 関心語の取得元 | 候補の条件 |
| --- | --- | --- |
| 検索指示あり | 指示から作る検索計画 | 見出し・抜粋に検索語の全単語を含み、言語・期間・除外語の条件に合う |
| 検索指示なし | Goodから得た上位3話題 | 学習した関心語に一致し、話題の重みを合計すると正になる |

`discovery_prompt`は2000文字まで。検索計画はqueries（最大3件、各150文字）、language（ja/any）、exclude_terms（最大8件）、max_age_days（1〜30日）で表す。複雑な指示は担当AIで整理し、25秒で完了しなければ入力文の先頭150文字と解釈できた条件を使う。適用条件と制限は結果に添える。AIが返したURLを取得する経路は持たない。

### 実行のきっかけ

| 操作 | 保存と検索 |
| --- | --- |
| 指示の入力 | 変換確定後650msで設定を保存し、変更後の条件で検索する |
| 「今すぐ探す」 | 現在の入力を保存し、その条件で1回検索する。同じ条件ならrefresh APIを使う |
| 定期実行 | 有効時に設定間隔（既定24時間、6〜168時間）で検索する |

保存要求を直列化し、版番号で古い待機要求を省く。実行中の変更は最新設定を1件だけ予約する。結果には対応するprompt/planを保存し、現在の指示と一致する結果だけを表示する。通信失敗時は入力を保持して再試行できる。

### 取得と保存

Bing/Google Newsの公開検索RSSから候補ページを得て、RSS/Atomリンクや一般的なフィードパスを検証する。日付不明・古い記事・登録済み配信元は除外する。取得は公開IP検査・DNS固定・転送回数・容量/時間制限・XMLのDTD/entity拒否を適用する。最大30サイト、6並列、取得全体10秒/結果待ち90秒。

検索サービスには検索語だけを送り、記事履歴・メモ・登録URL一覧・関連記録は送らない。条件整理のAIには入力指示と関心語を渡す。結果はmetadataへ保存し、空振り・失敗の再試行間隔は15分。`GET /api/discovery`で状態を読み、`POST /api/discovery/refresh`で非同期検索を要求する。

## 拡張共通の画面枠

ヘッダーにホーム・設定・テーマを置き、44px以上のタップ領域とライト/ダークの配色を共通にする。テーマはlocalStorageの`xangi-theme`（system/light/dark）へ保存し、端末設定とstorageイベントに同期する。

## タブ別の分類条件

記事一覧の8タブで種類・話題・自分のタグ・配信元・記事検索・配信元名検索を独立して保持する。localStorageのxangi-feed-classification:<tab>を使用し、未設定なら空の条件を復元する。切替直前に表示中の入力を保存し、切替先の条件と操作表示を復元してから取得する。検索デバウンスの未実行入力も切替時に保存する。

分類候補と配信元の初期読込完了前は保存・復元を始めない。値は許可済みの種類・話題・現存タグ・現存配信元へ限定し、不正JSONや削除済みの値は復元しない。「すべて解除」は現在のタブの条件だけを空にする。localStorageが使えない時も画面内のMapでタブ間の独立を保つ。おすすめは独自の検索設定を持つため記事一覧の分類保存に含めない。

## リンクカード

`show_link_previews` はbooleanで初期true。設定APIで保存し、healthから表示側へ反映する。一覧・記事詳細でIntersectionObserverにより画面付近のカードだけ取得する。期限内のキャッシュは一覧・詳細APIへ画像BLOBを含めずまとめて添付し、追加のメタ情報リクエストなしで描画する。未取得時の完了確認は最初の4回だけ250ms間隔、その後は1500ms間隔とする。カードの開き方と読了は既存articleLinkを共用する。

`GET /api/items/{id}/preview` は認証済みの登録記事に限り非同期取得を要求し、pending/busy/done/error/disabledと安全な文字列・has_imageを返す。`GET /api/items/{id}/preview/image` は同じ認証を通して保存したPNG/JPEG/GIF/WebPだけを配信する。画像も同一originなのでCSPのimg-srcはselfを維持し、ブラウザから外部画像へ直接接続しない。

`LinkPreviews` は既存の公開アドレス検証・DNS固定・転送先再検証付き取得を共用。HTMLはheadの終了までで取得を打ち切り、上限2MiB。通常のフィード取得・画像取得は最後まで読む。画像上限1MiB、取得ごとの接続8秒・全体20秒、ワーカー2・待ち件数50。OGPを優先しTwitter card・titleタグへフォールバックする。URLは元記事に固定し、リモートHTMLを表示・実行しない。画像なしや取得失敗でもカードのタイトルとサイト名を保持する。

専用link_previewsテーブルへメタ情報と画像BLOBを保存し、成功24時間・ページ失敗1時間をキャッシュする。保存は最新256件まで。OFF中は新しい取得・画像配信を行わず、既存キャッシュは保持する。記事本文・AI解説入力・好み・読了には取得処理から変更を加えない。

カードの構成は画像・見出し・サイト名・説明のまま、最大幅400px、画像高さは画面幅に応じ96〜160pxとする。キャッシュ・画像取得の際は記事ID/URL/タイトルだけを読み、日本語解説やGood／Badの集計を読み込まない。

## 開発と画面例の更新

Python 3.11以上・uv・Node.jsを使う。依存と正式チェックは次のとおり。

```sh
uv sync --frozen
./scripts/check
```

READMEの画面例は、一時DBに架空の記事・配信元・AIコメントを入れて、実際の画面を撮影する。Chromiumが使える環境で次を実行すると`docs/images/`へPC・スマホのPNGを出力する。

```sh
uv run --with playwright python scripts/docs-screenshots.py
```

Chromiumの場所は`PLAYWRIGHT_CHROMIUM_EXECUTABLE`で指定できる。スクリプトは外部通信を遮断し、稼働DB・AI・公開フィードを使わない。リンクカードの図もスクリプト内のHTML/SVGから生成する。
