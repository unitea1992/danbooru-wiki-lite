#!/usr/bin/env python3
"""danbooru.db の検索。人間用 CLI とエージェント用 JSON 入口の両方を持つ。

使い方:
  python3 search.py "silver hair"              # 人間用（search アクション）
  python3 search.py get maid                   # アクション名 + 引数
  python3 search.py lookup maid swimsuit yuri  # 複数タグの一括確認
  python3 search.py export-csv out.csv         # tagcomplete 形式で書き出し
  python3 search.py --request-json '{"action":"search","query":"メイド"}'
  echo '{"action":"status"}' | python3 search.py --request-json

JSON の応答は共通封筒形 {ok, action, results, truncated, next_offset, db}。
エラーコードと終了コードは DESIGN 第5.1節の表に従う。
"""
import csv, json, os, re, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCHEMA_VERSION, normalize

DB_PATH = os.environ.get("DANBOORU_DB", "danbooru.db")
MAX_RESPONSE = 20000
LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


class ApiError(Exception):
    def __init__(self, code, message, hint="", exit_code=1):
        super().__init__(message)
        self.code, self.message, self.hint, self.exit_code = code, message, hint, exit_code


def check_fts5():
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        con.close()
    except sqlite3.Error:
        raise ApiError("internal", "この Python の SQLite で FTS5 が使えません",
                       "FTS5 有効な Python/SQLite を使ってください", 1)


class Dict:
    def __init__(self, path=DB_PATH):
        if not os.path.exists(path):
            raise ApiError("db-missing", f"DB がありません: {path}",
                           "python3 fetch.py && python3 build.py を実行してください", 3)
        self.con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row
        uv = self.con.execute("PRAGMA user_version").fetchone()[0]
        if uv != SCHEMA_VERSION:
            raise ApiError("schema-mismatch",
                           f"スキーマ版が合いません (db={uv}, 期待={SCHEMA_VERSION})",
                           "python3 build.py で再構築してください", 3)
        self.meta = {k: v for k, v in self.con.execute("SELECT key, value FROM meta")}
        if self.meta.get("complete") != "1":
            raise ApiError("db-incomplete", "構築途中の DB です",
                           "python3 build.py を最後まで実行してください", 3)

    def tag_info(self, name):
        row = self.con.execute(
            "SELECT category, post_count, is_deprecated FROM tags WHERE name=?",
            (name,)).fetchone()
        if row is None:
            return {"category": None, "post_count": None, "is_deprecated": False}
        return {"category": row["category"], "post_count": row["post_count"],
                "is_deprecated": bool(row["is_deprecated"])}

    def wiki_row(self, normalized_title):
        return self.con.execute(
            "SELECT * FROM wiki WHERE normalized_title=? AND is_deleted=0",
            (normalized_title,)).fetchone()

    def resolve_name(self, name):
        """aliases を辿って正規名を返す。(正規名, 経路リスト)"""
        cur, chain, seen = normalize(name), [], set()
        while cur not in seen:
            seen.add(cur)
            row = self.con.execute(
                "SELECT consequent_name FROM tag_aliases WHERE antecedent_name=?",
                (cur,)).fetchone()
            if row is None:
                break
            chain.append(cur)
            cur = normalize(row["consequent_name"])
        return cur, chain

    # --- アクション ---
    def search(self, query, limit=10, min_post_count=None):
        q = normalize(query)
        limit = max(1, min(int(limit), 50))
        results, seen = [], set()

        def add(title, match):
            if title in seen or len(results) >= limit:
                return
            seen.add(title)
            info = self.tag_info(title)
            if min_post_count is not None and \
                    (info["post_count"] or 0) < min_post_count:
                return
            results.append({"tag": title, "match": match, **info,
                            "deprecated": info["is_deprecated"]})

        canonical, chain = self.resolve_name(q)
        if chain:
            add(canonical, "alias")
        row = self.wiki_row(q)
        if row:
            add(row["title"], "exact")
        elif self.tag_info(q)["post_count"] is not None:
            add(q, "exact")
        for r in self.con.execute(
                "SELECT wiki_id FROM wiki_other_names WHERE normalized_name=?", (q,)):
            w = self.con.execute("SELECT title FROM wiki WHERE id=?",
                                 (r["wiki_id"],)).fetchone()
            if w:
                add(w["title"], "exact")
        like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.con.execute(
            """SELECT w.title FROM wiki w LEFT JOIN tags t ON t.name=w.title
               WHERE w.is_deleted=0 AND w.normalized_title LIKE ? ESCAPE '\\'
               ORDER BY COALESCE(t.post_count,0) DESC LIMIT ?""",
            (like + "%", limit * 3)).fetchall()
        for r in rows:
            add(r["title"], "prefix")
        for r in self.con.execute(
                """SELECT w.title FROM wiki_other_names n JOIN wiki w ON w.id=n.wiki_id
                   WHERE n.normalized_name LIKE ? ESCAPE '\\' LIMIT ?""",
                (like + "%", limit * 3)):
            add(r["title"], "prefix")
        if len(results) < limit:
            tokens = [t for t in q.split("_") if t]
            if tokens:
                match = " AND ".join('"' + t.replace('"', '""') + '"' for t in tokens)
                try:
                    rows = self.con.execute(
                        """SELECT w.title FROM wiki_fts f JOIN wiki w ON w.id=f.rowid
                           WHERE wiki_fts MATCH ? ORDER BY rank LIMIT ?""",
                        (match, limit * 3)).fetchall()
                    for r in rows:
                        add(r["title"], "fts")
                except sqlite3.Error:
                    pass
            # 日本語など FTS で拾いにくい語は別名の部分一致でも拾う
            if any(ord(c) > 127 for c in query):
                for r in self.con.execute(
                        """SELECT w.title FROM wiki_other_names n
                           JOIN wiki w ON w.id=n.wiki_id
                           WHERE n.name LIKE ? LIMIT ?""",
                        ("%" + query + "%", limit * 3)):
                    add(r["title"], "fts")
        return results

    def get(self, tag, offset=0, raw=False):
        row = self.wiki_row(normalize(tag))
        if row is None:
            return {"tag": tag, "found": False}
        body = row["body"] if raw else row["body_clean"]
        body = body or ""
        chunk = body[offset:offset + 4000]
        nxt = offset + 4000 if offset + 4000 < len(body) else None
        return {"tag": row["title"], "found": True, "body": chunk,
                "next_offset": nxt, **self.tag_info(row["title"])}

    def lookup(self, tags):
        out = []
        for t in list(tags)[:20]:
            n = normalize(t)
            row = self.wiki_row(n)
            info = self.tag_info(n)
            item = {"tag": t, "found": row is not None or info["post_count"] is not None,
                    "has_wiki": row is not None, **info,
                    "deprecated": info["is_deprecated"]}
            if row is not None:
                item["summary"] = (row["body_clean"] or "")[:200]
            out.append(item)
        return out

    def resolve(self, tag):
        canonical, chain = self.resolve_name(tag)
        row = self.wiki_row(canonical)
        info = self.tag_info(canonical)
        return {"tag": tag, "canonical": canonical, "chain": chain,
                "found": row is not None or info["post_count"] is not None,
                "has_wiki": row is not None, **info}

    def implications(self, tag, depth=1):
        n = normalize(tag)
        depth = max(1, min(int(depth), 5))
        implies, frontier, seen = [], [n], {n}
        for _ in range(depth):
            nxt = []
            for cur in frontier:
                for r in self.con.execute(
                        "SELECT consequent_name FROM tag_implications"
                        " WHERE antecedent_name=?", (cur,)):
                    c = normalize(r["consequent_name"])
                    if c not in seen:
                        seen.add(c)
                        implies.append(c)
                        nxt.append(c)
            frontier = nxt
        implied_by = [normalize(r["antecedent_name"]) for r in self.con.execute(
            "SELECT antecedent_name FROM tag_implications WHERE consequent_name=?", (n,))]
        return {"tag": tag, "implies": implies, "implied_by": implied_by}

    def wiki_links(self, tag):
        row = self.wiki_row(normalize(tag))
        if row is None:
            return {"tag": tag, "found": False}
        links, seen = [], set()
        for target in LINK_RE.findall(row["body"] or ""):
            t = normalize(target)
            if t not in seen:
                seen.add(t)
                links.append(t)
        return {"tag": row["title"], "found": True, "links": links,
                "see_also": json.loads(row["see_also"] or "[]")}

    def status(self):
        m = self.meta
        out = {"schema_version": int(m.get("schema_version", 0)),
               "built_at": m.get("built_at"), "snapshot_date": m.get("snapshot_date"),
               "fetch_started_at": m.get("fetch_started_at"),
               "fetch_finished_at": m.get("fetch_finished_at"),
               "max_id": int(m.get("max_id", 0)), "counts": {}}
        for k, v in m.items():
            if k.startswith("count_"):
                table = k[6:]
                delta = None
                if f"prev_count_{table}" in m:
                    delta = int(v) - int(m[f"prev_count_{table}"])
                out["counts"][table] = {"count": int(v), "delta": delta}
        return out

    def export_csv(self, path, min_post_count=0):
        aliases = {}
        for r in self.con.execute(
                "SELECT antecedent_name, consequent_name FROM tag_aliases"):
            aliases.setdefault(r["consequent_name"], []).append(r["antecedent_name"])
        n = 0
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            for r in self.con.execute(
                    "SELECT name, category, post_count FROM tags"
                    " WHERE COALESCE(post_count,0) >= ? ORDER BY name",
                    (int(min_post_count),)):
                al = ",".join(sorted(aliases.get(r["name"], [])))
                w.writerow([r["name"], r["category"], r["post_count"], al])
                n += 1
        return {"path": path, "rows": n}


ACTIONS = {
    "search": ({"query", "limit", "min_post_count"}, lambda d, a: d.search(
        a["query"], a.get("limit", 10), a.get("min_post_count"))),
    "get": ({"tag", "offset", "raw"}, lambda d, a: d.get(
        a["tag"], a.get("offset", 0), a.get("raw", False))),
    "lookup": ({"tags"}, lambda d, a: d.lookup(a["tags"])),
    "resolve": ({"tag"}, lambda d, a: d.resolve(a["tag"])),
    "implications": ({"tag", "depth"}, lambda d, a: d.implications(
        a["tag"], a.get("depth", 1))),
    "wiki-links": ({"tag"}, lambda d, a: d.wiki_links(a["tag"])),
    "status": (set(), lambda d, a: d.status()),
    "export-csv": ({"path", "min_post_count"}, lambda d, a: d.export_csv(
        a.get("path", "danbooru_tags.csv"), a.get("min_post_count", 0))),
}


def run_request(req):
    if not isinstance(req, dict) or "action" not in req:
        raise ApiError("bad-request", "action が必要です", "", 2)
    action = req["action"]
    if action not in ACTIONS:
        raise ApiError("bad-request", f"不明な action: {action}", "", 2)
    allowed, fn = ACTIONS[action]
    unknown = set(req) - allowed - {"action"}
    if unknown:
        raise ApiError("bad-request", f"不明なキー: {sorted(unknown)}", "", 2)
    d = Dict()
    try:
        result = fn(d, req)
    except KeyError as e:
        raise ApiError("bad-request", f"引数が不足しています: {e}", "", 2)
    dbinfo = {"schema_version": SCHEMA_VERSION, "built_at": d.meta.get("built_at")}
    results = result if isinstance(result, list) else [result]
    env = {"ok": True, "action": action, "results": results,
           "truncated": False, "next_offset": None, "db": dbinfo}
    if isinstance(result, dict) and result.get("next_offset") is not None:
        env["next_offset"] = result["next_offset"]
    text = json.dumps(env, ensure_ascii=False)
    while len(text) > MAX_RESPONSE and len(env["results"]) > 1:
        env["results"].pop()
        env["truncated"] = True
        text = json.dumps(env, ensure_ascii=False)
    return env


def fail(e, as_json):
    if as_json:
        print(json.dumps({"ok": False, "action": None,
                          "error": {"code": e.code, "message": e.message,
                                    "hint": e.hint}}, ensure_ascii=False))
    else:
        print(f"エラー[{e.code}]: {e.message}", file=sys.stderr)
        if e.hint:
            print(f"ヒント: {e.hint}", file=sys.stderr)
    sys.exit(e.exit_code)


def main():
    argv = sys.argv[1:]
    as_json = "--request-json" in argv
    try:
        check_fts5()
        if as_json:
            rest = [a for a in argv if a != "--request-json"]
            raw = rest[0] if rest else sys.stdin.read()
            try:
                req = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ApiError("bad-request", f"JSON が読めません: {e}", "", 2)
            print(json.dumps(run_request(req), ensure_ascii=False))
            return
        if not argv:
            print(__doc__)
            return
        head, rest = argv[0], argv[1:]
        if head == "search" and rest:
            req = {"action": "search", "query": " ".join(rest)}
        elif head in ACTIONS and head != "search":
            if head == "lookup":
                req = {"action": "lookup", "tags": rest}
            elif head == "export-csv":
                req = {"action": "export-csv",
                       "path": rest[0] if rest else "danbooru_tags.csv"}
            elif head == "status":
                req = {"action": "status"}
            else:
                req = {"action": head, "tag": " ".join(rest)}
        else:
            req = {"action": "search", "query": " ".join(argv)}
        env = run_request(req)
        for r in env["results"]:
            if req["action"] == "search":
                print(f"{r['tag']}  [{r['match']}]"
                      f"  category={r['category']} posts={r['post_count']}"
                      + ("  ※廃止タグ" if r["deprecated"] else ""))
            elif req["action"] == "get" and r.get("found"):
                print(f"# {r['tag']}\n{r['body']}")
                if r.get("next_offset") is not None:
                    print(f"…続きは offset={r['next_offset']} で")
            else:
                print(json.dumps(r, ensure_ascii=False, indent=1))
        if env["truncated"]:
            print("（結果が上限で切り捨てられました）")
    except ApiError as e:
        fail(e, as_json)
    except Exception as e:  # 予期しない内部エラー
        fail(ApiError("internal", str(e), "", 1), as_json)


if __name__ == "__main__":
    main()
