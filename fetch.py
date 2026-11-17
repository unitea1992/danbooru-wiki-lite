#!/usr/bin/env python3
"""Danbooru 公式 API から Wiki / tags / aliases / implications を全件取得する。

標準ライブラリのみ。出力は data/*.jsonl.gz.building（正本への差し替えは
build.py が DB 検査を通した後に行う）。既定は全件取り直し。

使い方:
  python3 fetch.py [--data-dir data] [--skip-tags] [--max-pages N]
                   [--user-agent "..."]

- Wiki は番号ページ方式（page=1..N, limit=1000, id で重複排除）。
  本収集の後に、収集開始日以降に更新されたページを取り直して上書きする。
- tags は ID カーソル（page=b{id}）＋ post_count>=1 の絞り込み
  （2026-11-17 にカーソルの単調性・非重複を実測確認済み）。
- aliases / implications は status=active のみ。
- 429 / 5xx は Retry-After を見て指数バックオフで再試行する。
"""
import argparse, gzip, json, os, sys, time
import urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import now_iso

BASE = "https://danbooru.donmai.us"
DEFAULT_UA = ("danbooru-wiki-lite/0.1.0 "
              "(+https://github.com/unitea1992/danbooru-wiki-lite)")
LIMIT = 1000


def fetch_json(path, params, ua, retries=5):
    url = f"{BASE}{path}?" + urllib.parse.urlencode(params)
    delay = 2.0
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
            wait = e.headers.get("Retry-After")
            time.sleep(float(wait) if wait else delay)
            delay *= 2
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
            time.sleep(delay)
            delay *= 2


def numbered(path, params, ua, max_pages=None):
    """番号ページ方式で全件を yield する（list を返すジェネレータ）。"""
    page = 1
    while True:
        rows = fetch_json(path, {**params, "limit": LIMIT, "page": page}, ua)
        if not rows:
            return
        yield rows
        if max_pages and page >= max_pages:
            return
        page += 1
        time.sleep(0.2)


def fetch_wiki(ua, max_pages=None):
    started = now_iso()
    start_date = started[:10]
    by_id = {}
    params = {"only": "id,title,body,other_names,is_deleted,updated_at"}
    for rows in numbered("/wiki_pages.json", params, ua, max_pages):
        for r in rows:
            by_id[r["id"]] = r
        print(f"wiki page done, total={len(by_id)}", flush=True)
    # 収集中に更新されたページの取り直し（開始日以降）
    params2 = {**params, "search[updated_at]": f">{start_date}"}
    refetched = 0
    for rows in numbered("/wiki_pages.json", params2, ua, max_pages):
        for r in rows:
            by_id[r["id"]] = r
            refetched += 1
        print(f"wiki refetch done, refetched={refetched}", flush=True)
    return [by_id[k] for k in sorted(by_id)], started


def fetch_tags(ua, max_pages=None):
    """ID カーソル方式。post_count>=1 に絞る。name で重複排除。"""
    by_name = {}
    cursor = None
    pages = 0
    while True:
        params = {"only": "id,name,category,post_count,is_deprecated",
                  "search[post_count]": ">1", "limit": LIMIT}
        if cursor is not None:
            params["page"] = f"b{cursor}"
        rows = fetch_json("/tags.json", params, ua)
        if not rows:
            break
        for r in rows:
            by_name[r["name"]] = r
        cursor = min(r["id"] for r in rows)
        pages += 1
        print(f"tags page={pages} total={len(by_name)} cursor={cursor}", flush=True)
        if max_pages and pages >= max_pages:
            break
        time.sleep(0.2)
    return [by_name[k] for k in sorted(by_name)]


def fetch_pairs(endpoint, ua, max_pages=None):
    params = {"only": "antecedent_name,consequent_name,status",
              "search[status]": "active"}
    out = []
    for rows in numbered(endpoint, params, ua, max_pages):
        out.extend(rows)
        print(f"{endpoint} total={len(out)}", flush=True)
    return out


def write_gz(path, rows):
    tmp = path + ".building"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return tmp


def main():
    ap = argparse.ArgumentParser(description="Danbooru から全件取得する")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--skip-tags", action="store_true",
                    help="tags の取得を省略する（Wiki 等のみ）")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="テスト用: 各エンドポイントの取得ページ上限")
    ap.add_argument("--user-agent", default=DEFAULT_UA)
    args = ap.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)
    started = now_iso()
    jobs = []
    wiki, wiki_started = fetch_wiki(args.user_agent, args.max_pages)
    jobs.append(("wiki_pages", wiki))
    if not args.skip_tags:
        jobs.append(("tags", fetch_tags(args.user_agent, args.max_pages)))
    jobs.append(("tag_aliases",
                 fetch_pairs("/tag_aliases.json", args.user_agent, args.max_pages)))
    jobs.append(("tag_implications",
                 fetch_pairs("/tag_implications.json", args.user_agent, args.max_pages)))
    for name, rows in jobs:
        write_gz(os.path.join(args.data_dir, name + ".jsonl.gz"), rows)
        print(f"wrote {name}: {len(rows)} rows", flush=True)
    meta = {"fetch_started_at": wiki_started, "fetch_finished_at": now_iso()}
    with open(os.path.join(args.data_dir, "fetch_meta.json.building"),
              "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    print("FETCH_DONE", meta)


if __name__ == "__main__":
    main()
