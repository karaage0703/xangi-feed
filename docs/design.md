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

## UIの文字と密度の基準（推奨）

一覧タイトル16px・抜粋と一覧内の解説14pxで強弱を付けます。詳細の長文本文は16pxを維持します。

本体のファイラーを基準に、画面を切り替えても一覧・操作の密度が変わりにくい既定値を使います。拡張名・アイコンは本体の拡張開発ガイドに合わせ、既存IDを自動改名しません。これは新規UI・保守時の推奨で、利用者指定と用途に合う表示を優先します。例外申請や必須SDKは不要です。

- 一覧の主文字・ボタン・ナビ・通常ラベル: 13px（`--xangi-font-ui`）。
- 日時・説明などの補足: 12px（`--xangi-font-caption`）。短い件数やバッジ: 11px（`--xangi-font-micro`）。主要情報を小さい文字へ押し込みません。
- 画面名: 18px（`--xangi-font-heading`）。節見出し: 16px（`--xangi-font-section`）。一覧を巨大な見出しで占有しません。
- 記事・メール・Markdown・会話など読む本文: 16px、行高1.6〜1.75（`--xangi-font-reading`）。長文の見出しは24/18/16pxを目安にします。
- コード・ファイル編集: 13px、等幅（`--xangi-font-code`）。グラフ等の主要数値は24px（`--xangi-font-metric`）。

管理UIは端末のシステム書体を優先し、日本語などの代替書体はブラウザが選びます。本文と長文の入力欄には元の書体指定を使います。ブラウザの文字拡大を妨げず、親iframeから全子要素へフォントを強制注入しません。各拡張が同名のCSS変数を持ち、単体でも同じ基準を使います。取り込んだレポートHTMLや外部プレビューの本文は書き換えません。

余白は4px単位、薄い境界・中立色と青の操作色、角丸6〜8pxを既存画面に合わせて使います。情報の配置・機能・読み順は維持します。スマホでは入力欄16px、操作領域44px程度を確保し、文字だけでタップ領域を決めません。画面名はPC/スマホとも18pxを基本にし、長文本文を画面幅だけで縮小しません。

PC/スマホ・明暗テーマで、実際のcomputed style、長いラベル、スクロール、フォーカス、入力と戻る操作を確認します。本体の中と単体の両方を確認し、未確認の実機条件を報告します。

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

- AI生成は本体のautomation sessionとchat SSEを標準とする。担当未設定なら本体の既定を使い、mailの担当は借りない。LOCAL_LLM設定の継承だけでは直接接続しない。明示的なXANGI_FEED_DIRECT_LLM=1の場合だけ、選択local-llmへ直接JSON生成（出力上限2048、thinking無効）できる。直接接続は本体の監視・停止・利用量記録の対象外。
- 待ち時間は既定300秒（1〜1800秒）、待ち行列200件。取得後は直近14日から既定30件（0〜100件）、一覧表示時は先頭50件を候補にする。
- 有効な記事ID・見出し/日本語の要点/コメント・参照IDを検証する。原題を保持した見出しは英字だけでも許可する。参照は渡した資料から最大3件で、未知・重複・他記事のIDを拒否する。
- 完全なJSON直後の正規の返信候補タグだけを除外する。不正な応答は元の待ち時間内に1回だけ再生成する。関連記録付きの応答で記事ID・参照IDなどの検証に失敗した場合は、再生成時に関連記録を外し、現在の記事だけを渡す。構文エラーだけなら関連記録を保持する。
- 入力hashが同じ解説を再利用する。Good／Badや表示条件の変更では失効させず、本文の変更中に生成した古い結果は採用しない。
- 再生成中・失敗時は、同じ入力に対する前回の要約とコメントを保持する。エラーは15分後の一覧取得で再試行でき、起動時にも再試行可能な直近エラーを最大200件復元する。
- 再起動時のpendingは復元し、processingは結果不明のerrorにする。自動解説停止中は新たにAIへ送らない。件数0でも一覧表示による追加は有効。

## 根拠付きAIコメント

コメントは記事の具体的な使い道・試す手順・注意点を説明する。関連記録は用途を考えるための参考資料で、本人の希望や予定を証明するものとして扱わない。検索結果に発言者・出所の確認情報がないため、AIの提案・自動処理・引用と本人の直接発言を区別できない場合は、本人の事実を断定せず用途や条件で述べる。

生成AIには記録の抜粋と参照照合用IDを渡し、ファイル名・パスを渡さない。IDはevidence_idsだけに使い、本文へ番号・記録ファイル名が出た応答は採用しない。出典のパスと抜粋は「コメント生成時に参照した記録」で確認できる。

番号や記録ファイル名に依存した保存済みコメントは文字列置換で作文せず、要約と元の保存値を保持して書き直し対象にする。AIが有効なら表示時の待ち行列へ追加し、完了時に表示を更新する。停止中・失敗時は書き直す操作を案内する。読みやすいコメントやAmazon S3などの技術名だけの文章は、そのまま再利用する。

生成時の検索状態と、AIが実際に引用した記録のID・path・抜粋をcomment_contextへ保存する。AI応答から出典pathを採用せず、サーバー側の検索結果だけを対応付ける。コメントと出典は同じanalysis結果へ保存する。search失敗は要約・コメント生成を止めず、空の根拠として明示する。

検証失敗後に記事本文だけで再生成した場合は、comment_context.statusをarticle_only、sourcesを空配列にし、その旨を画面へ表示する。degradedは検索方式の縮退を表すため、この再生成だけを理由にtrueにはしない。除外した記録を参照したことにはしない。

一覧と詳細に「AIコメント」と書いた吹き出しを表示し、要約と区別する。「コメント生成時に参照した記録」で固定した当時の抜粋を開ける。後から行う手動検索の結果とは別に保持する。根拠がない/未取得/未参照のコメントは記事内容のみが材料であることを表示する。

吹き出しの生成・作り直し操作は既存analyze APIを使い、根拠を再検索して要約とコメントを生成する。searchの更新だけでは古いコメントを書き換えない。更新中・更新失敗は前回の内容に状態を添える。コメントや根拠は好みの点数・並び順へ加算しない。保存を依頼した場合は生成済みAIコメントと参照pathも保存用snapshotに含める。

## 好み・配信元の表示

Store.itemとlist_itemsの両方でpreference_scoreとpreference_knownを返す。同じ計算を使い、既知の0と評価なしを区別する。rating/reason更新の応答にはpreference_weightsを含め、ブラウザは表示中の記事の点数を更新する。表示順は変えない。

itemsのpublication_name / authorは空文字を既定とする。RSS／Atomのchannel/feed titleとauthor/creator、entry source、HTMLのog:site_nameとauthor、YouTube videoDetails.authorを保存する。取得失敗や情報欠落で既存値を空にしない。一般的なサービス名で固有名を上書きしない。

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
- `GET /api/items?tab=recommended|discovery|all|manual|saved|notes|read|discussed|muted&sort=updated|published|preference|recommended|read_at|manual_added_at&kind=tech|blog|news|comic|rss|podcast|paper|video&q=&source=&limit=50&offset=0`: 一覧。sortの既定値は読了でread_at、個別追加でmanual_added_at、それ以外はupdated。最大100件/ページ。
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

カスタム一覧では優先する要素を表示し、期間等の条件をdetailsへ格納する。フォームは単一とし設定ダイアログ側に複製しない。保存には既存POST /api/settingsを使用し、変更時に自動保存・再表示する。保存後も現在のtabを維持する。GET /api/recommendationsは既存のカスタム設定だけを返し、エージェント列挙やNotion設定の読み取りを伴わない。初回読み込み成功までfieldsetを無効化し、未取得の初期値で保存しない。

記事一覧と詳細の原文は通常anchor（target=_blank、rel=noopener noreferrer）を基本とし、後述のiPhone PWAのSafariアプリ起動モードではOSハンドラのURIへ切り替える。クリックをpreventDefaultせず、タップでブラウザが直接別画面を開く。元のフィードは遷移しない。共有・コピーdialogは記事のメニューから明示的に開く補助機能で、主操作に挟まない。

原文リンクはブラウザのユーザー操作として扱われるnative anchorを使う。xangiホストは同じページへのlocation.assignで拡張を開き、サービスproxyから拡張のCSPを配信する。iPhoneのホーム画面版では、表示形式やSafariの起動先がOSのバージョン・設定に依存する。実機での挙動は別途確認する。

仕様参照: [WebKit User Activation API](https://webkit.org/blog/13862/the-user-activation-api/)、[MDN Window.open](https://developer.mozilla.org/en-US/docs/Web/API/Window/open)、[Apple WWDC23](https://developer.apple.com/videos/play/wwdc2023/10120/)。

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

## 読了状態と一覧の更新

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

GET /api/items?tab=manualはmanually_added=1を件数・ページングの前に選ぶ。カスタム条件・読了による除外はせず、ミュート済みは除外し、種類・検索・配信元・タグとのANDは維持する。記事の種類（ブログ・YouTubeなど）は個別追加によって変更しない。「個別追加」は一覧切替メニューに表示し、一覧と詳細のメタ情報に個別追加の印を表示する。空状態から記事追加へ進める。

既存の配色・フォーカス・横スクロール可能な表示メニューを維持。PCとスマホで「個別追加→記事追加→詳細→戻る→種類/検索→読了→再表示」を確認する。参考: https://design.digital.go.jp/dads/components/tab/ （表示目的の切り替えと絞り込みを区別し、種類は既存の分類コントロールを使う）。


## おすすめ検索

「おすすめ」は公開Webの探索で見つけた未読記事の一覧。記事の表示・検索・分類・評価・ブックマーク・メモ・タグ・読了・ミュート・共有・AIコメント・議論・保存依頼は、各一覧で同じ記事データとUIを使う。操作は[おすすめの記事](usage.md#おすすめの記事)を参照。

### 記事の一覧と状態

見つかった記事は「すべて」にも表示する。読了にすると「おすすめ」から外れ、「すべて」「読了」に残る。未読に戻すとおすすめへ戻り、ミュート中の記事は「ミュート」から解除できる。

記事の識別にはcanonical URLを使う。同じURLの探索結果・配信元の記事・個別追加は同じIDを参照し、評価・メモ・タグ・読了日時・ブックマーク・会話・保存依頼を保持する。検索条件の変更や空振りで記事を削除せず、過去に見つけた記事も未読の間はおすすめに残る。配信元は自動登録しない。

### 検索条件と個人記録

`discovery_prompt`は2000文字まで。指定した話題・言語・除外語・期間から検索計画を作る。指定がなければGoodから得た上位3話題を使う。

検索計画はqueries（最大3件、各150文字）、language（ja/any）、exclude_terms（最大8件）、max_age_days（1〜30日）を持つ。複雑な指示は担当AIで整理する。25秒で完了しない場合は入力文の先頭150文字と解釈できた条件を使い、制限を設定内に表示する。見出し・抜粋に検索語の全単語を含む記事、またはGoodによる関心語に一致して話題の重みの合計が正になる記事が候補になる。言語・期間・除外語は候補の確認時にも適用する。

`discovery_use_personal_context`はboolean、初期true。ONで担当AIとhost接続がある場合、xangi-searchのhybrid検索から関連記録を最大3件（抜粋各1200文字）取得する。検索には最近の作業・課題と入力条件/Good上位話題を使う。日付で絞るAPIは使用しないため、最新記録の網羅は保証しない。

担当AIは記録から最大3テーマを作り、記録中の日付・進行状態を参考に具体的な作業とのつながりを説明する。明示した言語・期間・除外条件を保持し、参照IDと理由を検証する。取得・計画失敗、関連テーマなし、不正な参照IDの場合は入力指示とGoodによる検索計画を使う。記録検索は10秒、テーマ作成は最大25秒。公開探索には90秒から経過時間を引いた残りを渡す。

公開検索へ送るのは検索語だけで、記事履歴・メモ・登録URL一覧・関連記録の本文やパスは送らない。記録の抜粋は担当AIへ渡す。検索語は一般的な技術名・手法・用途へ変換するよう指示し、明白なパス・URL・メール形式は出力検証で拒否する。任意の秘密文字列を完全検出する仕組みではない。記録利用をOFFにすると新しい探索での参照を止め、取り込み済みの記事や推薦根拠は保持する。

### 操作と定期実行

- 検索指示の入力は変換確定後650msで保存し、その条件で検索する。
- 「今すぐ探す」は現在の入力を保存して1回検索する。同じ条件ならrefresh APIを使う。
- 定期実行は既定24時間、6〜168時間で設定できる。空振り・失敗の再試行間隔は15分。

保存要求を直列化し、版番号で古い待機要求を省く。実行中の条件変更は最新設定を1件だけ予約し、変更前の結果を取り込まずに検索し直す。通信失敗時は入力を保持して再試行できる。

画面の「おすすめの設定」には検索条件・更新日時・次回予定・記録の利用状況を表示する。一覧には検索中や失敗時の通知を表示する。

### 公開ページの確認

Bing/Google Newsの公開検索RSSから候補ページを得て、RSS/Atomリンクや一般的なフィードパスを確認し、1回の探索で最大6件を返す。日付不明・古い記事・登録済み配信元は除外する。取得は公開IP検査・DNS固定・転送回数・容量/時間制限・XMLのDTD/entity拒否を適用する。最大30サイト、6並列、取得全体10秒/結果待ち90秒。AIが返したURLを直接取得する経路は持たない。

### 保存形式とAPI

記事本体はitems、おすすめ由来の関連と推薦根拠はdiscovered_items、検索計画と実行状態はmetadataに保存する。新規の探索記事はcontent_origin=discovery、manually_added=0とし、個別追加の一覧には含めない。確認済みの抜粋・公開日・配信元名・音声URLを保存する。

記事・おすすめとの関連・探索状態は同じtransactionで保存する。起動時にもmetadataの候補と記事の関連を整え、同じ処理を繰り返しても重複や読了解除が起きないようにする。Good/Badの集計は探索処理で変更しない。

- `GET /api/items?tab=discovery`: おすすめ由来の未読・非ミュート記事。検索・分類・タグ・並び替え・ページ分割はlist_itemsで処理する。
- `GET /api/discovery`: 設定・検索計画・実行状態。
- `POST /api/discovery/refresh`: 現在の設定で非同期の検索を要求する。

候補が実際に一致した検索語をmatched_queryで返し、そのテーマの理由・使用した記録を記事のdiscovery属性へ添える。各一覧と記事詳細では同じ推薦根拠を表示する。本文の明示取得は公開ページの記事取得処理を使う。

## 拡張共通の画面枠

単体表示ではヘッダーにホーム・設定・テーマを置く。本体の埋め込み表示では共通ホームとテーマを本体に任せ、設定などの業務操作だけを残す。分類と記事の領域は本体ナビの内側から広げ、中央寄せの余白を追加しない。44px以上のタップ領域とライト/ダークの配色を保つ。テーマはlocalStorageの`xangi-theme`（system/light/dark）へ保存し、端末設定とstorageイベントに同期する。

## タブ別の分類条件

旧tab APIの分類条件は従来のlocalStorage経路を保持する。現在の画面は後述する「保存した一覧と共通フィルター」に統一し、分類・状態・好み等をサーバに保存する。配信元候補の検索文字列やパネル幅は端末ごとに保持する。

分類候補と配信元の初期読込完了前は保存・復元を始めない。値は許可済みの種類・話題・現存タグ・現存配信元へ限定し、不正JSONや削除済みの値は復元しない。「すべて解除」は現在のタブの条件だけを空にする。localStorageが使えない時も画面内のMapでタブ間の独立を保つ。おすすめの探索設定は、記事一覧の分類条件とは別に保存する。

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

スマホでは小さなアプリ名と分類を開くボタン・設定を上部の1段に置きます。更新・記事追加・検索は業務領域に残します。

一覧上部では、分類の選択チップを本文へ重ねずclassification-controls内で管理します。スマホの分類入口に選択数を表示し、詳細からタグで絞り込んだ後のフォーカスは分類入口へ戻します。タブ・検索・カスタム条件の余白を詰め、初期状態の自動保存説明は常時表示せず、保存・エラー等の状態は引き続き表示します。

スクロールでスキップの操作検証: `uv run --offline --with playwright python tests/scroll_skip_browser.py /absolute/path/to/evidence`。必要なら `CHROMIUM_EXECUTABLE` で既存のChromiumを指定。fixtureのみでPC/393px/360pxの初期OFF、ON/OFF保持、実表示後の上端通過、未表示・上方向・ブックマーク・読了・ダイアログの除外、ページ送り、解除、保存失敗と再試行、localStorage拒否を確認する。

## 保存した一覧と共通フィルター

画面は左メニューの一覧選択、右の常設検索・頻用操作、記事一覧で構成する。スマホでは既存のドロワーに一覧メニューを置き、選択後に閉じる。保存する分類・状態・好みの上限下限・期間・キーワード条件は、一覧管理とは独立した絞り込みダイアログへ集約する。固定タブの区別を画面からなくし、読了やスキップも編集可能な初期保存条件として扱う。名前だけは明示更新し、フィルターは自動保存する。

GET /api/views は初回に一度だけ旧標準カスタム設定と名前付き項目を引き継ぎ、カスタム/おすすめ/すべて/個別追加/ブックマーク/メモ/読了/議論した記事/スキップの9初期一覧を保存する。metadata/custom-views-v1を移行マーカーとし、以後は編集・削除した初期一覧を戻さない。以前のAPIは維持する。

各一覧はid/name/recommendations/kinds/topics/tags/source/search/sort/conditionsを持つ。conditionsはread(any/unread/read)、muted(exclude/include/only)、saved/noted/discussed(any/yes/no)、origin(any/manual/discovery)、max_preference(null/-100..100)。旧項目でconditionsがなければ未読・スキップを除く条件を補う。更新時にconditionsを省略した旧APIは既存条件を保持する。

POST /api/views/save はidと全フィルターを受け、最新レコードの名前を保持して更新する。GET /api/items?custom_filter=<id> は保存した分類・検索・状態・好みの範囲をサーバ側で適用し、ページング前に絞る。読了・スキップの既存データを書き換えない。上下限を指定すると未評価を除き、上限<下限は拒否する。好み・新しさ順以外の並び順や読了追加順・個別追加順も各一覧で選べる。

順序はmetadata/custom-filter-orderへ保存する。既存のPOST /api/custom-filters/reorderで全ID列を検証し原子的に更新する。初期一覧も順序・名前・条件・削除の対象。通常追加は100件まで、初期一覧9件を含む最大109件の移行と並べ替えを許容する。

UIは保存を直列化して差分がない復元では再保存しない。保存完了を待って一覧を切り替える。失敗時は入力を残し、切替を止めて再試行を案内する。読了・スキップ・ブックマーク等の操作後は共通フィルターで再判定する。おすすめ探索は取得機能として保持し、origin=discoveryの一覧で探索設定を表示する。

UI検証: tests/filter_separation_browser.py。旧タブ・ページ内設定前提のブラウザ検証は新しい左メニュー/ダイアログ構成へ読み替える。サーバの旧tab APIは互換経路として回帰検証を継続する。

## 読む操作と一覧管理の分離

左メニューの各一覧に設定ボタン（…）を設け、名前・複製・削除・順序はview-settings、保存条件は独立したfilter-dialogで編集する。スマホでも同じ入口から開く。条件は種類を先頭に表示し、状態、好み・期間、タグ・配信元・キーワードに折りたたみ、長大なフォームで記事一覧を押し下げない。条件は従来どおり自動保存し、失敗・再試行はダイアログ内と一覧上部の両方で扱う。

読む画面には一覧名、常設一時検索、絞り込みの入口と並び順を置く。各記事はreadの明示ラベル（未読/読了、動画は未視聴/視聴済み）と直接の切替ボタンを持つ。メニューの操作名から状態を推測させない。

searchは一時検索、saved-searchはサーバの保存キーワード条件の編集欄。GET /api/items?custom_filter=<id>&search=<temporary> は保存キーワードと一時検索をANDで適用し、ページング前に絞る。search_query引数は200文字まで。保存データはGETで更新しない。一時検索は設定の自動保存・複製に含めず、一覧切替・再読込で解除する。

配信元のnative selectは非表示のモデルとし、size=7と200px固定表示を廃止する。source-pickerは検索欄と候補ボタンを持ち、表示は先頭50件に制限する。候補検索だけで選択を変えず、候補の決定後に自動保存し、閉じたら起点へフォーカスを戻す。

左メニューの「RSS 配信元」は RSS／Atom 取得の登録元を表示し、選択した配信元の取得済み記事を通常一覧で開く。各保存一覧の矢印は、開いた時に `GET /api/views/source-ids?id=<id>` で保存条件に合う記事をページ分割前に集計し、その登録元のサブメニューを表示する。サブメニューからの選択は `sidebar_source` を保存条件と AND で適用する一時フィルターで、一覧名を選ぶと解除する。UI 検証は `tests/sidebar_rss_browser.py`。

UI検証: tests/filter_separation_browser.py。旧横タブや管理と条件の共通画面を前提とした任意ブラウザ検証は現行UIを対象としない。管理と絞り込みの分離、一時検索の保存非変更、配信元選択、タグ追加・解除、保存失敗とモーダル内再試行、独立context復元をPC/393px/360pxで確認する。

読了操作は既存のicon-button/action-iconと同じチェックアイコンへ統一する。常時の未読/読了ラベルは維持し、aria-label/titleで操作、aria-pressedで状態を伝える。読了条件の編集・確認は絞り込み内に統一する。追加の大きな操作ボタンを増やさない。

## 一覧管理と絞り込みの入口分離

サイドバーの…はview-settingsに名前/複製/削除/順序のみを表示する。条件はview-filter-openから別のfilter-dialogへ開く。読了条件はfilter-dialog内のselect一つに統一し、quick-readを削除する。種類は最初に表示する。一覧上部の条件要約と定型説明は表示しない。

話題タグと自分のタグにはそれぞれ選択解除（絞り込まない）を置く。空集合は条件なしで、タグがない記事も含む。選択解除は記事のタグを削除しない。話題はfeeds.TOPICSの固定分類、自分のタグは記事への明示追加で増える候補であることを説明する。

POST /api/views/renameはid/nameのみを受け、transaction内で最新の保存条件を読み名前だけを変更する。メタ設定の名前変更でフィルターを再保存せず、view-settingsを閉じるだけでも条件は更新しない。UI検証はtests/filter_separation_browser.py。

## 読む画面の説明とAI更新

一覧上部の条件要約・定型説明を除き、条件は絞り込み内で確認する。取得/追加のボタンは44pxの高さ・中央揃えとし、ヘッダーの右端に揃える。スマホの取得アイコンは文字グリフではなくSVGとする。絞り込みと並び順は高さ36px・行高20px・中央配置を明示し、OSのネイティブ選択欄と文字の基準がずれにくいよう揃える。

AI更新revisionが変わるとGET /api/analyses?id=...で表示中の解説だけを100件ずつ読み、記事の見出しとeditorialのみ差し替える。記事順・追加ページ・メモ入力・フォーカスを保持し、loadVersionが変わった古い応答は適用しない。解説反映のボタンは表示せず、新着記事を読むボタンとは独立させる。失敗時は元の表示を残し、revisionを進めず次回pollで再試行する。APIは本文hashが一致する分析状態だけを読み、AI処理を開始しない。

AI出力の検証エラーは見出し/要点/AIコメントの読者向け表現にする。旧データのtitle_ja/summary_jaを含むエラーは表示時にも読み替える。検証失敗を生成成功として扱わず、原文と前回の成功結果を保持する。

検証: tests/test_analysis_refresh.py、tests/reader_cleanup_browser.py。PC/393px/360pxで表示中120件・記事順・メモとフォーカス・自動反映・旧エラー・反映再試行・新着表示の独立・ボタン配置を確認する。

## 一覧の並べ替えと読む位置の維持

左メニューの保存一覧は枠を外し、選択・hover時だけ背景を付ける。xangi-tasksのカテゴリ行を参考に、名前の左に28pxの移動つまみ、右に設定を置く。ポインターの5px移動からドラッグとし、行の上下半分で挿入位置を示す。通常の名前のクリックは選択のまま。HTML drag/dropとAlt+↑/↓、既存の上下ボタンも同じ並べ替えAPIへ保存する。失敗時は元のサーバ順を保つ。

表示中の保存一覧のブックマーク/既読/スキップは直ちに押下状態を示し、同じ記事の送信を抑止する。POST reactにcustom_filter/searchを渡し、サーバは既存のlist_items判定をonly_idsの一記事に絞ってview_matchesを返す。分類・状態・タグ・検索・好み等の条件を別実装で複製しない。更新対象の存在を検証してから同じtransaction内で更新・判定する。読了・スキップにした記事は状態保存後もstate.itemsと表示に保持する。リンクを開いた自動読了、スクロールでスキップも同じ。保持中はGood／Bad、ブックマーク、解説更新、追加読込でも操作を続けられる。失敗時はボタンを操作前へ戻して通知する。ブックマーク解除のみで条件から外れ、読書用に保持していない記事は従来どおり外す。

Good／Badは好みの値を更新しても保存一覧を再取得せず、現在の順序を保つ。バックグラウンドの一覧再取得は表示中のカードを差し替えず、取得できた記事の状態だけ更新する。明示的な一覧選択・検索・フィルター変更・再読込で保持を解除し、条件と順位を適用し直す。追加ページはGET /api/itemsの繰返しexclude_idに表示済みIDを渡し、offset=0で未表示の記事を取得する。サーバは同じ条件・順位を計算した後、件数とページングの前にこれらを除外する。読了・スキップや評価で元の一覧の件数・順位が変わっても、表示済み件数をoffsetにして未表示記事を飛ばさない。

プレビューは112pxの高さを先に確保し、画像と文字をその中へ収める。遅延画像取得・失敗で行の高さが変わらない。その他の再描画・AI差し替え・通知の挿入では表示中の最初の記事IDと画面内位置を基準に読む位置を維持する。明示的な一覧切替/検索/絞り込みは従来どおり再取得する。

検証: tests/reading_interactions_browser.pyで3幅のポインター/キーボード並べ替え・失敗/復元、プレビュー遅延、送信中の即時状態/重複抑止/失敗復元、120件のDOM順維持と一覧GETなし、読了保持とブックマーク条件からの脱落、既存のプレビューOFF保存を確認。tests/keep_read_browser.pyは手動・自動読了とスキップ後のGood、自動更新、65件の重複・欠落なしの追加読込、明示的な切替・再読込による保持解除を3幅で確認する。tests/test_saved_views.pyは一記事判定と全件判定の一致・不正viewで更新しないこと・保持済みIDを除いたページングを確認する。

## 評価・読了操作の応答

Good／Bad・理由・読了・ブックマーク・スキップは送信前に操作状態を表示する。同じ記事の操作は保存中だけ無効にし、失敗した場合は操作した項目を元へ戻して再試行できるようにする。一覧と詳細の両方へ反映し、通信中の再読込にも仮の状態を維持する。別の一覧へ移った後の応答では行の削除・フォーカス移動を行わない。

保存応答では記事全体や一覧を作り直さず、操作ボタン・状態・好みの点数・Bad理由だけを更新する。カスタム一覧の読了・スキップ・評価変更は読書位置を保つため記事を残す。評価による並び順や条件の再適用は「好みを反映して一覧を更新」で行う。ブックマーク解除による条件外の行は従来どおり除外する。

reactは存在確認・読了時刻更新・評価保存・応答用集計を同じtransaction内で行う。好みの集計は1回とし、記事詳細およびcustom_filterの一致判定へ同じ集計結果を渡す。長期間のキャッシュは持たず、次の操作には最新の保存状態を使う。

検証: `tests/test_reaction_response.py` と `uv run --offline --with playwright python tests/reaction_response_browser.py <output>`。実サービスを使わず、応答待ち・失敗・再読込・DOM保持をPC/スマホ幅で確認する。

### 一覧と記事詳細の応答

記事一覧は読了・ブックマーク・配信元などの条件をSQLiteで絞り込み、ページ確定後に表示分の解説・保存依頼・配信元を読み込む。解説の内容を含む検索とタグ条件はページ確定前に評価する。好みは評価済みの記事から毎回集計し、件数はプロフィール表示で読み込む。

3ペインでは選択した記事のタイトルと抜粋を一覧データから先に表示し、詳細の取得後に最新の内容と操作を表示する。先に選んだ記事への遅い応答は、後から選んだ記事や閉じた右ペインに反映しない。
