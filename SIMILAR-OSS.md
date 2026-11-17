# 同種 OSS・データセット調査レポート

調査日: 2026-11-15〜16
調査方法: GitHub／Web／Hugging Face を検索し、候補はすべて実際のリポジトリ／データセットページを開いて確認した。開けなかったものは第6節「未確認」に分離してある。スター数・ライセンスは各ページの表示どおり（最終更新はページから直接読めない場合が多く、作成日・コミット数・README 内の日付で目安を示す）。
前提: 既知の先行例 Nsi0512/danbooru-tag-search（MIT）は調査済みとして対象外。

## 0. 要約

**本当に「同種」と言えるものは狭義で 4 件だけだった。**
「Danbooru のタグ／Wiki をローカル DB（SQLite）化して検索する構築キット」という意味で Nsi0512/danbooru-tag-search と同種なのは、NegativeMeta/weeb-alexandria、GALIAIS/Danbooru-Tag-Database、Lindemellis/ComfyUI-XYZ-prompt-library の tagdb、JackBinary/danbooru-db の4件。確認済みの候補は全部で 28 件あるが、残りは補完 UI（tagcomplete 系）、タグリスト生成器、Posts メタデータのデータセット、API クライアント／ダウンローダといった「隣接領域」で、同種というより部品や材料の供給源である。

**設計に影響する発見 Top 3**

1. **FTS5 は「本体と分けた派生 DB」にするのが先行例の共通形だった。** SQLite+FTS5 の直接の先行例は weeb-alexandria と GALIAIS の2系統のみで、両方とも FTS5 を本体 DB に直埋めせず、サイドカー／runtime DB として分離し「正本からいつでも再生成できる生成物」と位置づけている。現 DESIGN.md の「単一ファイルに wiki_fts を含める」案は、先行例のない形である点に注意が要る（第7節で分離案を提案）。
2. **tagcomplete 形式 CSV が事実上の標準で、カテゴリ番号の扱いは先行者の失敗が明記されている。** 補完エコシステム全体が `tag,category,post_count,"aliases"` 形式で回っており、内部が SQLite でもこの形式のエクスポートがあると供給先が一気に広がる。一方、複数ソース統合でカテゴリ番号に +7 オフセットをした DraconicDragon は README で自ら強く非推奨とし「`service`（source）列を持たせよ」と書いている。最初から source 列を持つ設計が正解。
3. **aliases と implications の両方を持つローカル DB はほぼ空白で、差別化余地が大きい。** 本格的に両方を持つのは weeb-alexandria のみ。他は alias のみ（CSV 列）か、implications は公式 API 任せ。意味論の正本は公式 danbooru/danbooru（正規化は保存時に寄せ、旧名は alias に残す）。また Posts の初期構築は API で這わず parquet スナップショット（Shio-Koube、10.6M 行）から作り、差分だけ API、という設計が先行例で裏付けられた。既存の API クライアント（pybooru 等）は wiki／aliases／implications をカバーしないため、「標準ライブラリのみで薄く自作する」現方針は調査からも支持される。

その他の重要発見: 過去時点の語彙を再現する仕組み（スナップショット／cutoff date／alias rollback）が複数例で実装されている／補完候補は post_count 閾値で切るのが常識（Top100k、≥50、≥100）／翻訳・other_names はタグ本体と分けた別レイヤーにするのが主流／生成データ（auto）と手編集（manual）を分けて manual を上書きしない二層マージの実例がある。

---

## 1. カテゴリ1: タグ／Wiki のローカル検索・DB化ツール（同種の核）

| 名前 | スター | ライセンス | 何をするものか |
|---|---|---|---|
| [NegativeMeta/weeb-alexandria](https://github.com/NegativeMeta/weeb-alexandria) | 0 | MIT | Danbooru+e621+Gelbooru のタグ定義・Wiki・aliases・implications を統合した単一 SQLite（約866MB、タグ142万件）＋MCP 検索。FTS5 は別建てサイドカー DB で、元 DB の SHA-256 と行数で鮮度検証し壊れていれば本体 SQL へフォールバック |
| [GALIAIS/Danbooru-Tag-Database](https://github.com/GALIAIS/Danbooru-Tag-Database) ＋ [GALIAIS-Nodes](https://github.com/GALIAIS/GALIAIS-Nodes) | 0 | DB側 表示なし／Nodes AGPL-3.0 | 正本をテキスト（tags/*.jsonl、taxonomy、翻訳 PO）に置き SQLite は生成物とする辞書。Nodes 側は FTS5 付き軽量 `.runtime.db` へのコンパイラとタグ正規化 Resolver（中国語ラベル対応）を持つ ComfyUI パック。標準ライブラリ sqlite3 のみ |
| [Lindemellis/ComfyUI-XYZ-prompt-library（tagdb）](https://github.com/lindemellis/comfyui-xyz-prompt-library) | 1 | 表示なし | ComfyUI 内蔵の版管理付きローカル SQLite タグ補完（約66MB、post_count≥50 で約11.8万タグ）。prebuilt DB を Release 配布＋自前構築 CLI の二本立て。スナップショット切替・増分更新・過去日付時点の語彙再構築（Time machine）を持つ。README は 2026-11-03 更新と活発 |
| [JackBinary/danbooru-db](https://github.com/JackBinary/danbooru-db)（[HF データセット](https://huggingface.co/datasets/JackBinary/danbooru-db)） | 2 | リポジトリ表示なし（要個別確認） | general タグ（post_count≥1000、9,322件）の Wiki 本文を単一 SQLite（約36MB）に入れ、sqlite-vec 埋め込みで自然言語→タグのベクトル検索をする CLI。Wiki 取得 1 req/sec、フェーズ分割で再開可能。dtext 除去済み body_clean と see_also を別カラム化 |

**借りる価値／要らない点（件別）**

- **weeb-alexandria** — 借りる: FTS5 サイドカー＋ハッシュ検証＋フォールバックの運用設計、aliases/implications を active/inactive 込みで保持するスキーマ、「派生インデックスは再生成可能・Git 管理外」の割り切り。要らない: 866MB のデータ自体を配る方式（構築キットのみの方針と衝突）、MCP/Hermes 前提、キャラ外見の手作業キュレーション層。
- **GALIAIS** — 借りる: 「テキスト正本→SQLite はビルド成果物」という思想は構築キット方針と最も近い。全辞書 DB と検索用 runtime DB の二層化、validate コマンド。要らない: AGPL-3.0（Nodes 側。コード流用は避ける）、中国語ローカライズ／Weblate 前提の複雑さ。
- **Lindemellis tagdb** — 借りる: prebuilt 配布＋自前フル再構築 CLI の二本立て、スナップショット／版履歴で過去時点を再現する発想、alias マッチの理由表示、複数ソースを別ファイルに分け正は Danbooru とする衝突規則。要らない: ComfyUI プラグイン前提の UI 層、認証情報の平文 JSON 保存、日本語／中国語検索を意図的に捨てている点。
- **JackBinary** — 借りる: Wiki 本文のクリーニング規則（See Also 抽出、Posts 節以降カット＝body_clean 化）、カーソルページングによる取得、フェーズ分割＋再開可能な再構築。要らない: general のみ・post_count≥1000 の切り捨て、llama-cpp-python 等の重い依存、ベクトル検索主体で FTS5 層は流用不可。

### 周辺の古典・派生

| 名前 | スター | ライセンス | 何をするものか |
|---|---|---|---|
| [fire-eggs/Danbooru2021](https://github.com/fire-eggs/Danbooru2021) | 43 | MIT | Gwern の Danbooru2021/2022 ダンプ（BigQuery 経由）から SQLite を生成するスクリプト群＋Tkinter 絞り込みビューア。Images 約595万行／Tags 約60万行。静的ダンプ→SQLite 化の古典的実績。Wiki・aliases は扱わない |
| [Zaozaotvk40/danbooru-rag-mcp](https://github.com/Zaozaotvk40/danbooru-rag-mcp) | 7 | MIT | タグ約6万件（頻度100以下を除外）を multilingual-e5-small で埋め込み LanceDB にローカル構築、日英セマンティック検索を MCP で提供。「低頻度タグはノイズなので切る」閾値の根拠付けが参考になる。Wiki 本文は持たない |
| [danbooru/danbooru（公式本体）](https://github.com/danbooru/danbooru) | 2,935 | Other | Rails 製本体。aliases/implications の意味論の正本（`TagAlias.to_aliased` による保存時正規化、implication の推移的付与、Bulk Update Request 承認フロー）と、related_tag エンドポイント（cosine/Jaccard/overlap/frequency）の定義元。流用対象ではなく仕様参照用 |

---

## 2. カテゴリ2: オートコンプリート／タグ辞典系（tagcomplete 系）

| 名前 | スター | ライセンス | データの持ち方と特徴 |
|---|---|---|---|
| [DominikDoom/a1111-sd-webui-tagcomplete](https://github.com/DominikDoom/a1111-sd-webui-tagcomplete) | 2,809 | MIT | この分野の標準。CSV 同梱が正本（Danbooru 2022年11月時点 Top100k）。形式 `tag,category,post_count,"aliases"` が事実上の標準仕様になった。翻訳は別ファイル（2列 CSV）。元データは旧公式 tags.json ダンプを SQLite 化して切り出したもの。更新機構を持たず同梱データは凍結 |
| [newtextdoc1111/ComfyUI-Autocomplete-Plus](https://github.com/newtextdoc1111/ComfyUI-Autocomplete-Plus) | 196 | MIT | 起動時に HF から自動 DL する2ファイル構成: タグ CSV（post_count≥100 等で絞り込み）＋**共起 CSV**（タグペア＋共起数、関連タグ表示用）。alias 列に日中韓の別名を含めて多言語検索に対応。共起を別ファイルに分離する発想が related 設計に直結 |
| [michikora/ComfyUI-Autocomplete-Plus-Plus](https://github.com/michikora/ComfyUI-Autocomplete-Plus-Plus) | 15 | MIT | Plus のリファクタ版フォーク。タグ CSV 同梱、翻訳 CSV は利用者が持ち込む前提。カテゴリ絞り込みをクエリ構文（`/danbooru` 等）で扱う、使用履歴・お気に入りを候補順位に混ぜる |
| [pythongosssss/ComfyUI-Custom-Scripts](https://github.com/pythongosssss/ComfyUI-Custom-Scripts) | 3,236 | MIT | ComfyUI UI 拡張集。辞典自体は持たず、利用者がカスタムワードリストを管理し外部 CSV を取り込む「受け口」。補完エンジンとデータ配布の分離の実例 |
| [Kerrybuck-17/ComfyUI-TagAutocomplete](https://github.com/Kerrybuck-17/ComfyUI-TagAutocomplete) | 2 | MIT | tagcomplete の ComfyUI への忠実移植。merged CSV（221,787タグ）＋中国語翻訳 CSV（231,417件）を既定同梱し訳語検索を初期状態で有効化。データ形式を変えず互換を維持した点が実例として有用 |
| [GUntilB/Anima-Prompt-Generator](https://github.com/GUntilB/Anima-Prompt-Generator) | 14 | GPL-3.0 | ローカル LLM の出力を CSV 辞典（約33,800タグ／約7,500 alias）で検証・正規化する ComfyUI ノード。**auto（生成）／manual（手編集・後勝ち・上書き禁止）の二層構造**と、alias を別ファイルに分離する正規化が借りられる。「辞典は LLM 出力の検証に使う」用途提示も新しい |

**借りる価値（横断）**: tagcomplete CSV へのエクスポート互換／カテゴリ番号体系（Danbooru: 0=general, 1=artist, 3=copyright, 4=character, 5=meta）／翻訳は本体と分けた別レイヤー／post_count 閾値で候補を切る実測感覚（全件約107万件中 post_count>0 で約50万、>10 で約15万）／個人の使用頻度 DB は正規辞典と分離する。
**要らない点**: WebUI／ComfyUI 組み込みの JS 補完 UI、LoRA・Wildcard・Chants 補完、モデル別 CSV の量産。タグデータを同梱して更新せず凍結させる運用は真似しない。

---

## 3. カテゴリ5（近接）: 共起・関連タグ・正規化を持つツール

| 名前 | スター | ライセンス | 何をするものか |
|---|---|---|---|
| [echo-xianyu/Booru-Pictag-Get-MCP](https://github.com/echo-xianyu/Booru-Pictag-Get-MCP) | 20 | MIT | ローカル DB を持たず公式 API を直接叩く「ライブ API 型」。キャラタグと共起するタグの頻度表、Wiki 本文、**双方向の implications チェーン**、alias/fuzzy/other_names による正規名解決を返す MCP |
| ComfyUI-Autocomplete-Plus の共起 CSV（第2節） | 196 | MIT | 共起≥100 のタグペアを別 CSV で配り関連タグ表示に使う、体系立った唯一の配布例 |

**借りる価値**: related の具体仕様（共起頻度で順位付けし meta タグを除外）、implications は順方向だけでなく逆方向（派生→本体）も返す API 設計、誤記・他言語名からの自動解決フロー。Posts の related を第2 DB 化する際の出力形式の参考になる。
**要らない点**: オンライン API 依存（ローカル完結しない）、プロンプト整形の競合ルール等は別用途。

### タグリスト生成器（辞典の材料供給源）

| 名前 | スター | ライセンス | 何をするものか |
|---|---|---|---|
| [BetaDoggo/danbooru-tag-list](https://github.com/BetaDoggo/danbooru-tag-list) | 201 | 表示なし | Danbooru API から tagcomplete 形式 CSV を生成する単一スクリプト。最低 post 閾値、カテゴリ除外、**cutoff date**、**alias のロールバック（改名日で巻き戻す）**に対応。日次スクレイプ結果を HF に蓄積 |
| [DraconicDragon/danbooru-e621-tag-list-processor](https://github.com/DraconicDragon/danbooru-e621-tag-list-processor) | 48 | GPL-3.0 | 上記の拡張。Danbooru／e621（db_export）／Gelbooru を alias 状態フィルタ付きで CSV 化、GitHub Actions で毎月再生成。merged リストのカテゴリ +7 オフセットを作者自身が非推奨と明記し「service 列を持たせよ」と書いている（コード流用は GPL のため不可、設計の参考のみ） |

---

## 4. カテゴリ3: Danbooru 投稿メタデータの公開データセット

| 名前 | 規模 | ライセンス | 内容と位置づけ |
|---|---|---|---|
| [Shio-Koube/Danbooru-2026-parquet-metadata](https://huggingface.co/datasets/Shio-Koube/Danbooru-2026-parquet-metadata) | 10.6M 行、DL 月953 | **明記なし**（カード自体が無い） | 画像なし・Posts メタデータのみの Parquet。カラムは API posts.json のフラット化そのもので、`tag_string_*` がカテゴリ別に分割済み。先行リポジトリが参照する実物で、共起計算の初期投入に最適。Wiki／aliases は含まない。再配布は前提にしないこと |
| [nyanko-devs/danbooru2026](https://huggingface.co/datasets/nyanko-devs/danbooru2026) | 画像 11M+（WIP） | MIT（画像の著作権は別途明記） | danbooru2023 の後継。画像 WebP の tar 群＋ `metadata/posts.parquet`。ID mod 1000 バケット方式が大規模配布の前例。本体は巨大で posts.parquet 単体で足りる |
| [BootsofLagrangian/danbooru-multitier-captions-202606](https://huggingface.co/datasets/BootsofLagrangian/danbooru-multitier-captions-202606) | 11.53M 行、64列、約14.6GB | 未確認（README 抜粋範囲） | 画像なし。正規化カラム＋ `metadata_json` に API レコード全文を残す二層設計、モデル生成キャプション付き。生 JSON を残す設計はスキーマ変更に強い。キャプション列は共起用途には無駄 |
| [Mexes/danbooru-tag-taxonomy](https://huggingface.co/datasets/Mexes/danbooru-tag-taxonomy) | 112,570 タグ、DL 月2,263 | CC BY 4.0（分類部分） | ※Posts ではなくタグ側。Danbooru 公式 category の上に意味 slot（例 `clothing:footwear`、33〜34種）を再分類。aliases は list 型、confidence＋status で機械分類の不確かさを保持、revision でスナップショット固定可能 |
| [nyanko7/danbooru2023](https://huggingface.co/datasets/nyanko7/danbooru2023) | ID #6,857,737 まで、全体約8TB | MIT | 古典系列（Gwern Danbooru2021 の系譜）。メタデータは posts.json／tags.tar.xz 形式で parquet ではない。「年次スナップショット＋メタデータ別置き」モデルの源流だが形式は新世代が上 |
| [aipracticecafe/curated-danbooru-2026](https://huggingface.co/datasets/aipracticecafe/curated-danbooru-2026) | 約330K 件、DL 月3,562 | （ページ確認済み、分類上の参考） | AVIF 画像埋め込みの学習用キュレーション版。Posts メタデータの材料としては不適。カテゴリ別逆頻度の `tag_weight` 列の発想のみ参考 |

**使い分けの結論**: Posts の初期構築は Shio-Koube（API スキーマそのまま）か BootsofLagrangian（二層設計）を parquet のまま取り込んで SQLite 化するのが最短で、更新差分だけ API で取る。共起の表現は (a) `tag_string_*` を post_tags 正規化テーブルへ展開（SQLite 向き）、(b) parquet 上の配列カラム（DuckDB 向き、未確認の PixelPerv 方式）の2案が実在する。

---

## 5. カテゴリ4: Danbooru API クライアント／ダウンローダ

| 名前 | スター | ライセンス | 何をするものか |
|---|---|---|---|
| [LuqueDaniel/pybooru](https://github.com/LuqueDaniel/pybooru) | 119 | MIT | Python 同期クライアントの定番（2012年〜、低頻度メンテ）。Danbooru／Moebooru 両対応、サイト名プリセット＋認証。ただし README 範囲では wiki_pages／tag_aliases／tag_implications 専用メソッドは確認できず、Wiki 収集の主経路には足りない |
| [sinkaroid/booru](https://github.com/sinkaroid/booru) | 34 | MIT（README 内の表記に揺れあり） | Python 非同期（asyncio/aiohttp）、19種の booru 対応。タグ補完 API のラッパー `find_tags` を持つ。汎用抽象化のため Danbooru 固有エンドポイントには踏み込まない。小規模で依存に抱えるより設計参考向き |
| [mikf/gallery-dl](https://github.com/mikf/gallery-dl) | 20,516 | GPL-2.0 | 最大手 CLI ダウンローダ（開発本拠は Codeberg へ移行済み、GitHub はミラー）。extractor プラグイン構造と、ダウンロードアーカイブによる差分取得の発想が借りられる。画像取得が主目的で GPL のため参照／外部呼び出しに留める |
| [Bionus/imgbrd-grabber](https://github.com/Bionus/imgbrd-grabber) | 3,237 | Apache-2.0 | C++/Qt の GUI+CLI ダウンローダ。カスタムソース定義ファイル（Danbooru 型 API のパラメータ化）、ファイル名トークン、ブラックリスト仕様が設定設計の参考。スタックが無関係で辞典構築の部品にはならない |
| [KohakuBlueleaf/HakuBooru](https://github.com/KohakuBlueleaf/HakuBooru) | 103 | Apache-2.0 | text-image データセット作成器。「クローラの多用は bot spam 化するので、事前ダンプ済みメタデータ DB（SQLite）＋画像 tar からローカルで作る」という思想が、新リポジトリの思想に最も近い先行例の一つ。Wiki／辞典機能はない |

**結論**: API クライアントに「決定版」は存在しない。pybooru も sinkaroid/booru も wiki／aliases／implications をカバーしないため、タグ収集は標準ライブラリのみで薄く自作する現方針が妥当で、先行品はエンドポイント対応表の照合先として使うのがよい。

---

## 6. 未確認（検索には出たがページを開いて検証していないもの）

以下は存在の痕跡のみで、スター数・ライセンス・内容は引用元の主張として扱い、設計判断の根拠にしていない。

- **Devildonia/HaDeS-Booru** — Civitai 記事でのみ確認。ローカル SQLite のオフラインタグ辞書（人気約1,000タグ、alias 解決付き）との記載。GitHub 本体未開封。
- **xiaoshengyvlin/Zako-Prompt-Tools（Zako-Danbooru-Search）** — comfy.icu 解説経由。約30万件の中英タグ対応表をローカル SQLite で引くとの説明で設計が近そうだが GitHub 本体未開封。次に調べる価値あり。
- **alchemine/comfyui-generator-pack の Tags Generator** — ノード説明のみ。548万投稿から作った共起テーブル（約106MB npz、20,811タグ）との説明。GitHub 本体未開封。
- **eduardoabreu81/sd-webui-tagcomplete-neo 系、bedovyy/tagcomplete-for-animagine-xl-3.0** — スニペットのみ。本体未開封（後者は辞典ではなくモデル別データセットの位置づけ）。
- **jupo-ai/comfy-ex-tagcomplete** — ページは開けたが古いキャッシュでスター／ライセンスが読めなかった（内容自体は tagcomplete 借用と確認済み）。
- **PixelPerv/danbooru-tags-20260518-duckdb** — ブログ記事＋HF 一覧の断片のみ。11.4M posts を DuckDB 形式・タグ配列カラムで持つとの主張。共起計算に理想的な形式のため、採用検討前に実ページでのライセンス／構成確認が必須。
- **Nandaka/DanbooruDownloader** — コミット／リリースページのみ出現（★396 前後、C# 製との断片情報）。本体 README 未開封。
- **qdlabs/danbooru-tags、isek-ai/danbooru-tags-2016-2023／2024、deepghs/site_tags、Jio7/danbooru-tags-classified** — 検索結果／Related 記載に出現したタグ一覧系データセット群。本体未開封のため評価対象外。

---

## 7. DESIGN.md に追加で反映すべき点（提案）

現行 DESIGN.md（v0.1）への差分提案。番号は同ファイル第6節「レビューで判断がほしい点」への追加・具体化を含む。

1. **FTS5 の置き場所を再検討する（原則2・第4節）**: 先行例2系統とも FTS5 を本体と分けた派生 DB にしている。選択肢を明記する: (a) 現案どおり単一 DB 内蔵、(b) FTS5 サイドカー DB（本体のハッシュ／件数で鮮度検証、再生成可能）。「派生インデックスは生成物であり正本ではない」という原則を1行足すだけでも設計の見通しが良くなる。
2. **tagcomplete 形式 CSV のエクスポートを v1 の検索アクション（または出力形式）に追加する（第5節）**: `export-csv` 相当があるだけで tagcomplete／Autocomplete-Plus／Custom-Scripts／SwarmUI／Krita への供給路が開ける。内部形式と配布形式を分ける方針を明記する。
3. **スキーマに `source` 列を最初から入れる（第4節）**: 将来 e621／Gelbooru を扱う場合にカテゴリ番号のオフセット統合をしないための保険。Danbooru 専用の間も `source='danbooru'` 固定で持つ。DraconicDragon の失敗談を根拠として出典付きで書く。
4. **aliases／implications の意味論は公式 danbooru/danbooru を正本と明記する（第3・4節）**: 正規化は「保存時に正規名へ寄せ、旧名は alias テーブルにだけ残す」。implications は直接のみか推移閉包まで持つか、順方向のみか逆方向も返すかを、Booru-Pictag-Get-MCP の双方向設計を参考に判断点として追加する。
5. **スナップショット概念を watermark に格上げする（原則5・第4節 meta）**: 構築時点の語彙であることを `snapshot_date` として保持し、cutoff date 指定での再構築（BetaDoggo／Lindemellis の Time machine の軽量版）を将来の拡張点として書く。全件再構築方式はこの「時点再現」と相性が良い。
6. **Wiki 本文のクリーニング工程を仕様化する（第4節）**: JackBinary の規則（dtext 除去、See Also を別カラム `see_also` 化、Posts 節以降カットした `body_clean` を併せて持つ）を採用候補として明記する。FTS5 の対象を body_clean にするかも判断点になる。
7. **Posts 第2 DB の初期構築経路を明記する（原則6）**: API 全件クロールではなく Shio-Koube の parquet スナップショットから変換し、差分だけ API で取る。あわせて同データセットはライセンス無表記であるため「データの再配布はしない（構築キットのみ）」という現方針の根拠として記録する。
8. **post_count の閾値をビルドパラメータとして設計に書く（第4節の検討点への回答材料）**: 補完用途では Top100k／≥50／≥100 が実績値。全件保持が原則でも、検索結果の提示や CSV エクスポートでは閾値で切れるようにする。
9. **翻訳・other_names は別テーブルにする（第4節）**: tagcomplete 系（別 CSV）と Autocomplete-Plus（alias 列混在）の2派があるが、DB が正本なら別テーブル＋検索対象に含める形が両派の利点を取れる。Wiki の other_names をここへ正規化する。
10. **手編集レイヤー（manual）を予約する（第4節）**: Anima-Prompt-Generator の auto／manual 二層（manual は後勝ち・再構築で上書きしない）は、個人の補正を公開キットに載せる受け皿として小さいコストで入れられる。
11. **ライセンス注意の一覧を第3節に足す**: コード流用不可＝DraconicDragon（GPL-3.0）、GALIAIS-Nodes（AGPL-3.0）、gallery-dl（GPL-2.0）。流用可だが帰属が必要なもの＝MIT／Apache-2.0 群と Mexes（CC BY 4.0）。「設計の参考」と「コード流用」を分けて書く現行方針を、この一覧で具体化する。
12. **API クライアント自作の根拠を第3節に追記する**: pybooru／sinkaroid-booru とも wiki／aliases／implications をカバーしないことを確認済み。「標準ライブラリのみで薄く自作する」原則1の外部裏付けになる。

