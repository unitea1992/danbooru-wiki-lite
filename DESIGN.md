# Danbooru ローカル DB 新リポジトリ 設計ドラフト v0.2

状態: v0.2（2026-11-17 改訂）。Claude Opus の設計レビュー（REVIEW-opus.md）と
同種 OSS 調査（SIMILAR-OSS.md）の反映が完了し、管理人の決定
（安全化と v0.2 反映は一括で実施／ハイブリッドプールの3層構造を採用）を
取り込んだ版。v1 の実装はこの版を正として進める。
変更履歴は末尾の第10節にある。

目的: 既存の自作 Danbooru Wiki 辞典（301,458件で初回構築、週次更新で
301,526件・2026-11-16 時点、SQLite+FTS5、305MB）と、
公開リポジトリ Nsi0512/danbooru-tag-search (MIT, 2026-11-15) の良い所を組み合わせ、
自分たちの設計思想で公開リポジトリとして作り直す。

## 1. 目的と非目的

目的:
- Danbooru のタグ情報をローカル DB 化し、ネットワークなしでタグ引き・意味確認・
  別名解決ができる道具を、誰でも再現構築できる形で公開する。
- 主な利用者は画像生成のプロンプト設計をする人と AI エージェント。

非目的:
- 構築済み DB（データ）の配布はしない。道具だけを公開する（先行リポジトリと同方針）。
- Danbooru 本体のミラーや投稿画像の取得はしない。扱うのはタグ／Wiki メタデータのみ。
- 汎用フレームワーク化はしない。小さく保つ。
- 個人プロジェクトである Danbooru×DLsite ハイブリッドプールそのものは
  このリポジトリの目的に含めない。関係は第8節に切り分けて書く。
  本リポジトリが担うのは、そのソース層にもなる汎用の辞典道具まで。

## 2. 設計原則（私たちの思想）

1. **小さいことが正義**: Python 標準ライブラリのみ。全体で「全部読める」規模
   （目安 1,000行以内、先行実装の約5,000行にはしない）。依存ゼロで数年後も動く。
   行数目安の内訳（レビュー指摘で追加）: fetch 250、build 150、検索コア 300、
   CLI／JSON 入口 150、残りは予備。テストは行数目安に含めない。
2. **検索品質が本体**: Wiki 本文の全文検索は FTS5 + rank を正とする
   （先行実装は LIKE 主体で、ここは自作版が勝っているので残す）。
   タグ名の完全一致・前方一致を先に、本文 FTS を後に、という自作版の提示順も維持する。
   あわせて、FTS インデックスは「正本からいつでも再生成できる生成物」であり、
   正本は取得した生データとスキーマである、と位置づける（SIMILAR-OSS 提案1の
   一部採用。第9節参照）。
3. **人間用 CLI とエージェント用 JSON の両方**: 人間には読みやすいテキスト、
   エージェントには 1リクエスト→1 JSON の構造化入口。どちらも同じ検索コアを使う。
4. **整合性は軽装備、ただし差し替えは原子的**: manifest 封印・検証レシート・
   承認フラグのような重い仕組みは入れない。その代わり、全件再構築で
   「途中の状態を持たない」ことを前提に、次の差し替え手順を必須とする
   （Opus 指摘①。現行 build_db.py が冒頭で旧 DB を os.remove する問題への回答）。
   1. 新しい DB は同じディレクトリの一時ファイル（例 `danbooru.db.building`）に
      構築する。構築が終わるまで旧 DB には一切触らない。取得した JSONL も、
      DB の構築と検査が全部終わるまでは正本側へ差し替えない。
   2. 差し替え前に検査する:
      - `PRAGMA quick_check` が `ok` を返すこと。
      - `INSERT INTO wiki_fts(wiki_fts) VALUES('integrity-check')` が通ること。
      - 各テーブルの件数が下限を超え、前回の件数（meta に記録）の
        **95% 以上**あること。API の仕様変更等で空に近い DB に
        置き換わる事故を防ぐ。
   3. meta に `schema_version`、`built_at`、取得開始・終了時刻、
      テーブルごとの件数、ID の最大値、`snapshot_date`、`complete=1` を書き、
      `PRAGMA user_version` を設定してから、`os.replace` で原子的に差し替える。
   4. 検索側は `file:...?mode=ro`（読み取り専用）で開き、`user_version` が
      合わない場合は `schema-mismatch`、`complete` が無い場合は
      `db-incomplete` の構造化エラーを返す（第5節）。
   5. 多重起動はロックファイルへの `fcntl.flock`（Windows では
      `msvcrt.locking`）で防ぐ。cron と手動実行が重なって同じ一時ファイルを
      2プロセスが書く事故を防ぐための十数行であり、「重いロック管理」には
      当たらない。なお Windows では検索中プロセスが DB を開いていると
      `os.replace` が失敗し得るため、差し替え失敗時は短い再試行を入れる。
5. **更新は単純に**: 既定は全件取り直し＋DB再構築（自作版で実証済み。
   Wiki 収集は約14分、tags を足すと合計 30〜40分程度の見込み、週1）。
   失敗時は旧 DB を残し、辞典が使えなくならない（原則4の手順で保証する）。
   差分更新は v1 では入れない。将来入れる場合は ID カーソルではなく
   `updated_at` 基準にする（レビュー 6-2）。
   あわせて収集の網羅性対策として次を全件再構築の一部とする（Opus 指摘②）:
   - 収集開始時刻 T を記録し、番号ページ方式の本収集が終わった後に、
     `search[updated_at]=>T` で
     **収集中に更新されたページだけを取り直して上書き**する
     （日付粒度 `>YYYY-MM-DD` の書き方は 2026-11-17 に実測で動作確認済みで、
     運用側 update_wiki.py に実装済み。時刻粒度の可否は未確認）。
     番号ページ方式は
     収集中の編集でページが先頭へ移り、取りこぼしが実績として起きているため。
   - 構築の最後に件数と ID 最大値を meta に記録し、前回との増減を
     `status` で見られるようにする。
   - 構築時点の語彙であることを `snapshot_date` として meta に保持する
     （SIMILAR-OSS 提案5を採用）。cutoff date 指定での過去時点再構築は
     将来の拡張点とし、v1 では実装しない。
6. **Posts は v1 の範囲外**: 共起数 count / 関連タグ related（約15GB の第2 DB）は
   標準ライブラリのみ・1,000行以内の原則1と両立しない（初回構築に
   第三者 Parquet スナップショットと pyarrow が必要なため。Opus 指摘③）。
   v1 のアクションから外し、将来課題として第7節に置く。本体だけで完結させる。

## 3. 取り込むもの／取り込まないもの

先行リポジトリ (Nsi0512/danbooru-tag-search, MIT) から:
- 構造化メタデータの考え方: tag 一覧（post_count, category, is_deprecated）、
  tag_aliases、tag_implications を Wiki 本文とは別テーブルで持つ。
- 検索アクション resolve（別名→正規タグ、Wiki ページが無いタグも引ける）、
  implications、wiki-links の概念。
- コードを流用する場合は MIT の著作権表示を残す。ただし原則は
  自分たちの小さい実装への書き直し（クリーンな再実装）を優先する。
  実装中は先行実装のコードを開いたまま書かない（流用の有無を曖昧にしないため）。

公式 danbooru/danbooru を意味論の正本とする（SIMILAR-OSS 提案4を採用）:
- aliases／implications の意味論は公式本体の定義に従う。正規化は
  「保存時に正規名へ寄せ、旧名は alias テーブルにだけ残す」。
- implications は直接の含意先を既定とし、`depth` 引数で推移的にたどれる
  ようにする。逆方向（派生→本体）の参照も返せるようにする
  （Booru-Pictag-Get-MCP の双方向設計を参考）。

API クライアントは自作する（SIMILAR-OSS 提案12を採用）:
- pybooru、sinkaroid/booru とも wiki_pages／tag_aliases／tag_implications の
  専用メソッドを持たず、Wiki 収集の主経路には足りないことを調査で確認した。
  したがって取得層は標準ライブラリ（urllib）で薄く自作し、先行品は
  エンドポイント対応表の照合先として使う。これが原則1の外部裏付けになる。

自作版から:
- FTS5 検索、番号ページ方式の収集（page=1..N, limit=1000, id dedupe）、
  全件再構築の更新スクリプト。
- 「失敗時に旧 DB を残す運用」は、実証済みだったのは全件取得と再構築の
  完走までで、失敗時の挙動は v0.1 時点では未検証だった、と訂正する。
  v0.2 では原則4の手順を v1 の受け入れ条件として保証する。
- 実測知見: カーソル方式 page=a{last_id} は wiki_pages の並び順と噛み合わず破綻した
  （HARVEST-NOTE 参照）。ID カーソルを採用するなら並び順の実証が前提。

取り込まないもの:
- manifest／検証レシート／承認フラグ等の封印機構一式（原則4の手順で代替する）。
- 動かないことが明記された refresh パイプラインのような参考実装の同梱。
- DuckDB 集計（SQLite で足りる間は入れない）。

ライセンス注意一覧（SIMILAR-OSS 提案11を採用。「設計の参考」と「コード流用」を分ける）:
- コード流用不可（設計の参考のみ）: DraconicDragon/danbooru-e621-tag-list-processor
  （GPL-3.0）、GALIAIS-Nodes（AGPL-3.0）、gallery-dl（GPL-2.0）。
- 流用可だが帰属表示が必要: MIT／Apache-2.0 の各プロジェクト、
  Mexes/danbooru-tag-taxonomy（CC BY 4.0、分類部分）。
- データセットの再配布はしない。特に Posts 用 Parquet スナップショット
  （Shio-Koube）はライセンス無表記であり、構築キットのみを配る方針の
  根拠の一つとして記録する（第7節）。

謝辞・出典（レビュー 6-6 を採用）:
- README に「着想を得た先行実装」として Nsi0512/danbooru-tag-search への
  謝辞を書く。コードを流用した場合は該当ファイル冒頭と NOTICE に
  先行実装の著作権表示を残し、「shares no code」の文言は外す。
- README に、データは Danbooru 公式 API から取得すること、Wiki 本文は
  Danbooru の利用者が書いたものであること、構築済み DB は配布しないこと、
  削除の要望を Issue で受け付けることを書く。

## 4. スキーマ案 v1（danbooru.db、単一ファイル）

- `wiki(id INTEGER PK, title TEXT, normalized_title TEXT, body TEXT,
  body_clean TEXT, see_also TEXT, is_deleted INTEGER, updated_at TEXT,
  source TEXT)` — Wiki 生データ（自作版互換を保ちつつ列を追加）。
  - `normalized_title`: 小文字化し空白を `_` に寄せた正規化名。索引を張り、
    タイトル完全一致・前方一致の検索を索引で引けるようにする（現行実装の
    `lower(title)` 全件走査をやめる）。
  - `body_clean` / `see_also`: 本文クリーニング工程の成果物（下記）。
  - 名前の正規化（小文字化、空白と `_` の扱い）は、構築・検索・resolve の
    3か所で同じ関数を通す。候補を複数作って試す方式は採らない。
- `wiki_fts` — **外部コンテンツ表**の FTS5
  （`content='wiki', content_rowid='id'`、対象は title と body_clean。
  Opus 指摘⑤。本文の複製を持たないため DB は約305MB→約180MB の見込み）。
  トークナイザは既定の `unicode61`（`remove_diacritics 2`）とする。
  porter による語幹処理と trigram は v1 では使わない。日本語名は本文 FTS
  ではなく `wiki_other_names` への LIKE 検索で扱う（名前の列は短く、
  全件走査でも足りる見込み。足りないと分かってから trigram を足す）。
- `wiki_other_names(id INTEGER PK, wiki_id INTEGER, name TEXT,
  normalized_name TEXT)` — Wiki の `other_names` を正規化して持つ別テーブル
  （Opus 指摘④、SIMILAR-OSS 提案9を採用）。日本語名からの逆引きと、
  第8節の対応付けの橋に使う。検索対象に含める。
- `tags(name TEXT PK, category INTEGER, post_count INTEGER,
  is_deprecated INTEGER, source TEXT)` — `source` は将来の複数ソース統合で
  カテゴリ番号のオフセット統合をしないための保険として最初から持ち、
  当面は `source='danbooru'` 固定とする（SIMILAR-OSS 提案3を採用。
  根拠は DraconicDragon が自ら非推奨とした +7 オフセットの失敗談）。
- `tag_aliases(antecedent_name TEXT, consequent_name TEXT, status TEXT)` —
  取得時に `status=active` のみへ絞る。
- `tag_implications(antecedent_name TEXT, consequent_name TEXT, status TEXT)` —
  同上。
- `meta(key TEXT PK, value TEXT)` — schema_version, built_at, snapshot_date,
  fetch_started_at, fetch_finished_at, テーブルごとの件数, max_id,
  complete 等の watermark。

v0.1 の検討点への決定:
- **wiki_links はテーブルにしない**。`wiki-links` アクションは検索のたびに
  本文から `[[...]]` を正規表現で抜き出して正規化して返す。逆リンクが
  必要になった時点でテーブル化を再検討する。
- **tags（post_count を含む）は v1 に入れる**。resolve の「Wiki のないタグも
  引ける」を成立させるためで、post_count は同じ応答に含まれ追加通信は要らない。
  前方一致の候補は post_count の順に並べる。
- 本文が空のページ（実測 70,447件、全体の23%）の扱い: 残す。ただし
  `other_names` も本文も空のページは FTS に入れない。`is_deleted` で
  削除済みと区別できるようにする。
- コロンを含むタイトル（`tag_group:`、`list_of:`、`howto:` 等、実測 4,274件）は
  tags と結合できない「メタページ」として扱い、検索結果では
  `category` を null にして返す。
- 手編集レイヤー（manual）は v1 ではテーブルを実装しない（SIMILAR-OSS
  提案10は保留。第9節）。個人の補正は公開 DB ではなく第8節の
  個人プロファイル層側で持つ。

### 4.1 取得仕様

- Wiki: 番号ページ方式（page=1..N, limit=1000, id dedupe）。
  取得列は `only=id,title,body,other_names,is_deleted,updated_at` に拡張する
  （Opus 指摘④）。本収集の後に、原則5の取り直し（収集開始時刻 T 以降の
  更新分）を1回行う。削除済みは `is_deleted` 列で保持し、検索側で扱いを分ける。
- tags: Wiki と同じ番号ページ方式は使わない。Danbooru の番号ページ上限
  （1,000ページ、要実測）に当たる見込みのため、tags だけは
  `page=b{id}` の **ID カーソル＋ `search[post_count]=>1` の絞り込み**で
  取得する計画とする（Opus 指摘③）。ID カーソルが tags の並び順と
  噛み合うかは、Wiki での破綻実績があるため、実装前に必ず実測で確認する。
  実測で使えない場合は代替（カテゴリ別分割取得等）を設計し直す。
  更新時間は tags 分で 15〜25分程度延びる見込みのため、
  `--skip-tags` 選択肢を用意する。
- aliases／implications: `status=active` のみを取得する。
- 公開道具としての配慮: User-Agent は「道具名/版＋リポジトリ URL」を既定とし
  利用者が上書きできるようにする。再試行は 429 と 5xx のときに
  `Retry-After` を見て指数的に待つ。cron 登録は README で案内するだけにし、
  既定では登録しない。実行間隔は「週1回以下」を推奨と明記する。

### 4.2 本文クリーニング工程（SIMILAR-OSS 提案6を採用）

- 生の `body` はそのまま保持し、併せて `body_clean` と `see_also` を作る
  （JackBinary/danbooru-db の規則を参考にする）。
  - `body_clean`: DText の軽い整形（`[[a|b]]`→`b`、`h4.`→見出し記号など）と、
    Posts 節以降のカットを行った本文。FTS の対象はこちらにする。
  - `see_also`: See Also 節のリンク先を抜き出して保持する。
- `get` の既定返却は body_clean 側の整形済み本文とし、`"raw": true` の
  ときだけ原文を返す（第5節）。

## 5. 検索アクション案

- `search` : キーワード検索。タグ名一致→前方一致→FTS本文、の順で提示。
  まず `tag_aliases` で別名を解決し、`alias` として先頭に出す。
  各結果に当たった種別 `match`（`exact` / `alias` / `prefix` / `fts`）と
  `category`、`post_count`、`is_deprecated` を付ける。廃止タグは
  `deprecated: true` を目立つ形で返す。前方一致の並びは post_count 順。
  提示件数は post_count の閾値パラメータで切れるようにする
  （SIMILAR-OSS 提案8を採用。補完用途の実績値は Top100k／≥50／≥100。
  全件保持は維持し、切るのは提示とエクスポート側）。
- `get`    : 1タグの Wiki 本文。本文は既定 4,000 文字で切り、
  `next_offset` で続きを読めるようにする。
- `lookup` : 複数タグの有無＋要約を一括で（最大 20タグ）。
- `resolve`: aliases を辿って正規タグ名を返す。Wiki 有無も併せて返す。
- `implications`: 直接の含意先を既定とし、`depth` で推移的にたどれる。
  逆方向の参照も返せるようにする。
- `wiki-links`: 本文から `[[...]]` を抜き出して正規化して返す（テーブル化しない）。
- `status` : 件数・構築時刻・snapshot_date・前回との増減などデータ鮮度。
- `export-csv`: tagcomplete 形式 CSV（`tag,category,post_count,"aliases"`）への
  エクスポート（SIMILAR-OSS 提案2を採用）。内部形式（SQLite）と
  配布・供給形式（CSV）を分けることで、tagcomplete／Autocomplete-Plus 等の
  補完エコシステムへの供給路を開ける。閾値パラメータで件数を切れる。

入口: `search.py "<query>"` の人間用 CLI と、`search.py --request-json` の JSON 入口の2つ。
JSON は標準入力から1つ読むのに加え、引数でも渡せるようにする。
不明なキーは無視せず `bad-request` にする。

### 5.1 JSON 応答の共通形とエラーコード（Opus 指摘⑦）

応答は共通の封筒（エンベロープ）にそろえる。

成功時:

```json
{"ok": true, "action": "search", "results": [...],
 "truncated": false, "next_offset": null,
 "db": {"schema_version": 1, "built_at": "2026-11-16T04:40:00+09:00"}}
```

失敗時:

```json
{"ok": false, "action": "get",
 "error": {"code": "db-missing", "message": "...", "hint": "python build.py を実行してください"}}
```

エラーコード一覧（小さい固定の集合に限る）と終了コードの対応:

| code | 意味 | 終了コード |
|---|---|---|
| （成功） | 正常終了 | 0 |
| `bad-request` | 入力 JSON の形式誤り、不明なキー、引数不足 | 2 |
| `db-missing` | DB ファイルが存在しない | 3 |
| `schema-mismatch` | `PRAGMA user_version` が期待するスキーマ版と合わない | 3 |
| `db-incomplete` | meta の `complete` が無く、構築途中の DB とみなされる | 3 |
| `internal` | 上記以外の予期しない内部エラー | 1 |

- 「見つからない」はエラーにしない。`lookup`／`get` で対象がないときは
  `ok: true` のまま `{"tag": "x", "found": false}` を返す。一括問い合わせの
  1件の不在で全体を失敗扱いにしないため。
- 出力上限: `search` は既定 10件・最大 50件、`lookup` は最大 20タグ、
  `get` は本文既定 4,000 文字、応答全体は 20,000 文字を上限とし、
  超えたら `truncated: true` を明示する。

### 5.2 テスト

- ネットワークなしで動くテストを用意する。データを配布しない方針に反しない
  よう、Danbooru の本文は同梱せず、自作した数十件の架空のタグと Wiki で
  小さな DB を作り、検索の順序と JSON の形を確かめる。
- 起動時に FTS5 が使えるかを確かめ、使えない環境では分かりやすい
  エラーを返す。

## 6. レビュー判断点への決定（v0.1 第6節の回収）

1. 原則4（整合性の軽装備）: **軽装備のままで十分**。ただし原子的差し替え・
   差し替え前検査・多重起動防止の3点を必須として原則4に明記した。
   manifest／検証レシート／承認フラグは入れない。
2. 全件再構築 vs 差分更新: **v1 は全件再構築を既定**とする。収集中の
   更新分の取り直しを組み込む。差分更新を将来入れるなら ID カーソル
   ではなく `updated_at` 基準。tags のみ ID カーソル（実測前提、第4.1節）。
3. FTS5 トークナイザ: **既定の `unicode61`（`remove_diacritics 2`）で足りる**。
   porter と trigram は v1 では使わない。日本語名は `wiki_other_names` の
   LIKE 検索で扱う。FTS は外部コンテンツ表にする。
4. wiki_links と tags.post_count: **tags は v1 に入れる。wiki_links は
   テーブル化しない**（第4節）。
5. JSON 入口: アクション名は先行実装と同じ
   （`search` / `get` / `lookup` / `resolve` / `implications` / `wiki-links` /
   `status`、本設計で `export-csv` を追加）。共通封筒形とエラーコードは
   第5.1節のとおり。
6. リポジトリ名と公開アカウント: 公開アカウントは unitea1992 配下とする。
   名前は **`danbooru-wiki-lite`** に決定（2026-11-17、管理人の選択。
   「小さい」「Wiki が中心」「Posts を扱わない」が伝わることを理由とする。
   先行の danbooru-db／Danbooru-Tag-Database／danbooru-tag-search とは
   被らないことを確認済み）。
   ライセンスは MIT。謝辞の書き方は第3節のとおり。

## 7. 将来課題（v1 の範囲外）

- Posts 拡張（`count` / `related`）: 原則6のとおり v1 から外す。将来やる場合は
  別リポジトリか任意依存の例外を明記した拡張とし、初期構築は API 全件
  クロールではなく Parquet スナップショットからの変換＋差分だけ API、
  という経路を前提にする（SIMILAR-OSS 提案7はこの条件付きで記録のみ。
  第9節）。同データセットはライセンス無表記であるため、出典と
  ライセンスの確認作業を着手条件に含める。再配布はしない。
- cutoff date 指定による過去時点の語彙再構築（snapshot の Time machine 化）。
- wiki_links の逆リンクが必要になった場合のテーブル化。
- FTS5 のサイドカー DB 化（容量が外部コンテンツ化でも問題になった場合。
  第9節の提案1）。
- 手編集レイヤー（manual）の公開キット側への実装（第9節の提案10）。

## 8. ハイブリッドプール（個人プロジェクト）との関係

このリポジトリとは別に、個人プロジェクトとして
Danbooru×DLsite ハイブリッドプールを構築する（2026-11-17、管理人が採用を決定）。
本リポジトリの辞典道具は、その土台の一つとして使われる。

考え方:
- **Danbooru**＝「目に見えるもの」の語彙。衣装・ポーズ・表情・状況を
  英語タグで細かく言えるプロンプト向きの辞書。ただし件数が膨大で、
  人間が直接間引く対象にはならない。
- **DLsite ジャンル**＝「作品テーマ」の分類。日本語・数百件規模で人が
  選んだ分類であり、関係性や「あざとい」系のように絵に直接は出ないが
  テーマの核になるものも拾える。人間が1回で間引き切れる規模である。
- 先駆事例（note の名鑑方式：嫌いなものを先に外したプールから抽選する）と
  同じ発想で、間引いたプールをシチュエーションの供給源にする。

3層構造（採用済み）:
1. **ソース層**: Danbooru DB（本リポジトリの道具で構築する辞典）＋
   DLsite ジャンル一覧（ID・名前・分類）。汎用データの層。
2. **対応付け層**: DLsite ジャンル → Danbooru タグ群の対応付け。
   多対多＋重みで持つ。対応付けの橋には、Wiki の `other_names`（日本語名）を
   使う。第4節で `wiki_other_names` を別テーブルとして取得する設計は、
   この橋を成立させるためのものでもある。
3. **個人プロファイル層**: 間引き結果（残す／外す、重み）と評価履歴を持つ、
   個人専用の層。人間が間引くのは数百件の粗い分類だけで、細部は
   対応付け層と Danbooru DB が自動で展開する形にする。

ギャラリーとの接続:
- 画像ギャラリー Space の日次生成は、既にプール抽選方式で運用されている
  （レパートリー全件から厳選したアクティブセットだけを抽選対象にし、
  直近数日と被らないよう重み付きで抽選する）。
- その**厳選アクティブセット（現行8件）とスワイプ評価の履歴**
  （favorite が付いたシチュエーションへの重み付け、reject が一定数以上で
  favorite が無いシチュエーションの自動卒業）が、個人プロファイル層の
  「種」として既に実在する。ハイブリッドプールはこれを取り込んで初期値とし、
  将来はプール側からギャラリーへ新しいシチュエーションを供給し、
  評価フィードバックを受け取る形で接続する。
- プロンプト組み立ては persona-image スキルが担い、タグ DB はそこへ
  接続する想定とする。

公開範囲の線引き（厳守）:
- **公開リポジトリに含めるのは構築キットのみ**。次は含めない:
  個人プロファイル層の実データ、対応付け層の実データ（対応付け結果と重み）、
  DLsite ジャンルの生一覧、間引き結果、評価履歴、構築済み DB。
- 層の構造やスキーマといった汎用的な設計の記述は公開側に書いてよいが、
  個人の選好そのものが読み取れる内容は書かない。
- **年齢が曖昧・未成年系のジャンル／タグは、プロファイル以前に既定で
  除外する**（成人向けのみとする既存ルールの適用）。ソース層・対応付け層の
  どちらの作業でも、この除外を先に適用してから間引きや対応付けを行う。
- NSFW の具体的な生成レシピや詳細はこのプロジェクトの範囲外とし、
  プールの分類と対応付けまでを扱う。

置き場:
- 運用 DB と自作版コードは `~/workspace/danbooru-hybrid/dict/` に置く
  （従来の `~/workspace/goals/comfyui-rocm/hidden_files/danbooru/` から
  移行済み。詳細は第11節の参照資料を参照）。
- ハイブリッドプールの個人データは `~/workspace/danbooru-hybrid/` 配下の
  非公開領域に置き、公開リポジトリの作業フォルダ
  （`~/workspace/danbooru-new-repo/`）とは物理的に分ける。

## 9. SIMILAR-OSS 提案12点への判定（付録）

SIMILAR-OSS.md 第7節の提案について、1点ずつ判定した。内訳は
**採用 9点／保留 3点／却下 0点**。

| # | 提案 | 判定 | 理由（短く）／反映先 |
|---|---|---|---|
| 1 | FTS5 サイドカー DB 化の再検討 | 保留 | 先行例の共通形ではあるが、原子的差し替えと単一ファイルの単純さが崩れる。v1 は外部コンテンツ表で約180MB 化し、「派生インデックスは再生成可能な生成物」という原則だけ先に採用する。容量が問題になったら第7節の将来課題として再検討（原則2、第4節、第7節） |
| 2 | tagcomplete 形式 CSV エクスポート | 採用 | stdlib の csv で安く実装でき、補完エコシステムへの供給路が一気に広がる。内部形式と配布形式を分ける（第5節 `export-csv`） |
| 3 | スキーマに `source` 列 | 採用 | 将来の複数ソース統合でカテゴリ番号オフセットの失敗を避ける保険として安い。当面 `source='danbooru'` 固定（第4節） |
| 4 | aliases／implications の意味論は公式を正本と明記 | 採用 | 意味論の正本を1か所に定めないと正規化がぶれる。implications の depth と逆方向も併せて仕様化した（第3節、第5節） |
| 5 | snapshot_date を watermark に格上げ | 採用 | 全件再構築と相性が良く、meta に1項目足すだけ。cutoff 再構築は将来の拡張点に留める（原則5、第4節、第7節） |
| 6 | Wiki 本文クリーニング（body_clean／see_also）の仕様化 | 採用 | FTS の質と `get` の可読性が上がり、正規表現程度で実装できる。FTS 対象は body_clean に決めた（第4.2節） |
| 7 | Posts 第2 DB の初期構築経路（Parquet 起点）の明記 | 保留 | Posts 自体を v1 範囲外としたため、経路は将来課題への記録に留める。着手時はライセンス確認を条件にする（第7節） |
| 8 | post_count 閾値をビルドパラメータ化 | 採用 | 全件保持と補完用途の絞り込みは両立する。提示とエクスポート側で閾値を切れるようにした（第5節） |
| 9 | other_names は別テーブルにする | 採用 | Opus 指摘④とも一致し、日本語逆引きとハイブリッドの橋の両方に必要（第4節 `wiki_other_names`） |
| 10 | 手編集レイヤー（manual）の予約 | 保留 | 受け皿の発想は良いが、v1 では個人の補正は個人プロファイル層側で持つ方が公開境界が明確。公開キットへの実装は将来課題（第4節、第7節） |
| 11 | ライセンス注意の一覧を第3節へ | 採用 | 「設計の参考」と「コード流用」の線引きを具体化でき、事故防止になる（第3節） |
| 12 | API クライアント自作の根拠を第3節へ | 採用 | pybooru 等が wiki／aliases／implications をカバーしないことは調査済みで、原則1の外部裏付けになる（第3節） |

## 10. 変更履歴

### v0.1 → v0.2（2026-11-17）

- REVIEW-opus.md の「v1 に入る前に決めてほしいこと」7点を全て反映:
  ①原子的差し替え手順（一時ファイル構築→quick_check・FTS integrity-check・
  件数検査（前回比95%以上）→meta 記録→os.replace、fcntl.flock による
  多重起動防止）を原則4に明記。
  ②収集開始時刻以降に更新されたページの取り直しを全件再構築の一部にした。
  ③tags 取得は ID カーソル＋post_count>=1 絞り込みとし、実測前提を明記。
  ④Wiki 取得列に other_names／is_deleted／updated_at を追加。
  ⑤FTS を外部コンテンツ表にし、正規化名カラム（normalized_title）に索引。
  ⑥Posts 拡張（count／related）を v1 範囲外とし将来課題へ移動。
  ⑦JSON 応答の共通封筒形とエラーコード一覧（bad-request／db-missing／
  schema-mismatch／db-incomplete／internal、終了コード対応）を第5.1節に追記。
- SIMILAR-OSS.md 第7節の提案12点を判定し反映（採用9／保留3／却下0。
  判定表は第9節）。主な採用: tagcomplete CSV エクスポート、source 列、
  公式意味論の正本明記、snapshot_date、body_clean／see_also、
  post_count 閾値パラメータ、other_names 別テーブル、ライセンス注意一覧、
  API クライアント自作の根拠。
- 第8節「ハイブリッドプール（個人プロジェクト）との関係」を新設。
  3層構造の採用、ギャラリーの厳選アクティブセットと評価履歴を
  個人プロファイル層の種として接続すること、公開範囲の線引き、
  年齢が曖昧・未成年系の既定除外を明記。
- 第6節を「レビューで判断がほしい点」から「レビュー判断点への決定」に
  書き換え、未決事項を回収した。
- 参照資料のパスを新しい置き場（~/workspace/danbooru-hybrid/dict/）に更新。

### v0.1（2026-11-16）

- ソニア作成の叩き台。設計原則、スキーマ案、Opus に判断してほしい6点を提示。

## 11. 参照資料

- 自作版コード・運用 DB: ~/workspace/danbooru-hybrid/dict/
  （harvest_wiki2.py, build_db.py, danbooru_search.py, update_wiki.py, HARVEST-NOTE.md）
  ※従来は ~/workspace/goals/comfyui-rocm/hidden_files/danbooru/ に置かれていたが、
  検証時のフォルダ命名だったため ~/workspace/danbooru-hybrid/ 配下へ移行した。
- 設計文書: ~/workspace/danbooru-new-repo/（DESIGN.md、REVIEW-opus.md、SIMILAR-OSS.md）
- 先行リポジトリ: https://github.com/Nsi0512/danbooru-tag-search (MIT)
- 公式本体（意味論の正本）: https://github.com/danbooru/danbooru
- 自作版 DB 実測: 初回 301,458件、週次更新で 301,526件（2026-11-16）、
  danbooru_wiki.db 305MB、検索動作確認済み
  （"silver hair" で grey hair / white hair への正規案内が返る）
