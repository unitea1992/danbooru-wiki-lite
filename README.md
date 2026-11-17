# danbooru-wiki-lite

Danbooru の Wiki（タグの定義文と別名）をローカル DB 化し、ネットワークなしで
タグ引き・意味確認・別名解決ができるようにする、小さな構築キットです。
Python 標準ライブラリのみで動き、構築済み DB は配布しません（道具だけを公開します）。

主な利用者は、画像生成のプロンプト設計をする人と AI エージェントです。

## 使い方

```bash
python3 fetch.py     # Danbooru 公式 API から全件取得（data/ へ、約15〜40分）
python3 build.py     # danbooru.db を構築（検査を通った場合だけ原子的に差し替え）
python3 search.py "silver hair"
python3 search.py get maid
python3 search.py --request-json '{"action":"search","query":"メイド"}'
```

- 取得は全件取り直しが既定です。実行間隔は**週1回以下**を推奨します。
  cron への登録は各自で行ってください（このキットは登録しません）。
- `fetch.py --skip-tags` で tags の取得を省略できます。
- DB の鮮度は `python3 search.py --request-json '{"action":"status"}'` で確認できます。

## 検索アクション

| アクション | 内容 |
|---|---|
| `search` | キーワード検索。別名解決→完全一致→前方一致→本文 FTS の順に提示。日本語名（Wiki の other_names）からも引けます |
| `get` | 1タグの Wiki 本文（既定は整形済み本文、4,000字ずつ） |
| `lookup` | 複数タグ（最大20）の有無と要約を一括確認 |
| `resolve` | 別名を辿って正規タグ名を返す |
| `implications` | 含意先を返す（`depth` で推移的に辿る。逆方向も返す） |
| `wiki-links` | 本文中の `[[...]]` リンクと See also を返す |
| `status` | 件数・構築時刻・snapshot_date・前回との増減 |
| `export-csv` | tagcomplete 形式 CSV（`tag,category,post_count,"aliases"`）への書き出し |

人間用 CLI と、エージェント用の JSON 入口（`--request-json`、標準入力または引数）
の両方があります。どちらも同じ検索コアを使います。JSON の応答形式と
エラーコードは DESIGN.md 第5節に仕様があります。

## テスト

```bash
python3 tests/test_search.py
```

ネットワークなしで動きます。Danbooru の本文は同梱せず、架空のタグと Wiki で
小さな DB を作って検索の順序と JSON の形を確かめます。

## データについて

- データは Danbooru 公式 API から取得します。Wiki 本文は Danbooru の利用者が
  書いたものです。
- 構築済み DB（データそのもの）は配布しません。各利用者が自分の手元で
  構築してください。
- 収録内容についての削除の要望は Issue で受け付けます。

## 設計

設計の正本は [DESIGN.md](DESIGN.md) です。先行実装の調査は
[SIMILAR-OSS.md](SIMILAR-OSS.md)、設計レビューは [REVIEW-opus.md](REVIEW-opus.md)。

## 謝辞

着想を得た先行実装 [Nsi0512/danbooru-tag-search](https://github.com/Nsi0512/danbooru-tag-search)
（MIT）に感謝します。本リポジトリは設計思想を参考にした再実装であり、
コードの流用はありません。

## ライセンス

MIT License. See [LICENSE](LICENSE).
