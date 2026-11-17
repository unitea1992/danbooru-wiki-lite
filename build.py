#!/usr/bin/env python3
"""取得データ（data/*.jsonl.gz）から danbooru.db を構築する。

差し替え手順（DESIGN 原則4）:
  1. 一時ファイル danbooru.db.building に構築し、旧 DB には触らない。
  2. quick_check / FTS integrity-check / 件数検査（前回の95%以上）を通す。
  3. meta を書き、user_version を設定してから os.replace で原子的に差し替える。
  4. 成功後に限り、取得データの .building を正本名へ昇格させる。
多重起動はロックファイル（fcntl.flock）で防ぐ。
"""
import argparse, gzip, json, os, re, sqlite3, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import SCHEMA_VERSION, normalize, now_iso

LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
H4_RE = re.compile(r"^h4\.\s*(.+)$", re.M)


def clean_body(body):
    """(body_clean, see_also) を返す。Posts 節以降を切り、[[a|b]] を b に開く。"""
    if not body:
        return "", []
    see_also = []
    m = re.search(r"^h4\.\s*See [Aa]lso\s*$", body, re.M)
    if m:
        rest = body[m.end():]
        nxt = re.search(r"^h4\.", rest, re.M)
        section = rest[:nxt.start()] if nxt else rest
        see_also = [normalize(t) for t, _ in LINK_RE.findall(section)]
    cut = re.search(r"^h4\.\s*Posts\b", body, re.M)
    text = body[:cut.start()] if cut else body
    text = LINK_RE.sub(lambda mm: mm.group(2) or mm.group(1), text)
    return text.strip(), see_also


def open_input(data_dir, name):
    """name.jsonl.gz.building があれば優先、無ければ name.jsonl.gz /
    name.jsonl を開く。無い場合は None。"""
    for cand in (name + ".jsonl.gz.building", name + ".jsonl.gz", name + ".jsonl"):
        p = os.path.join(data_dir, cand)
        if os.path.exists(p):
            if cand.endswith(".gz.building") or cand.endswith(".gz"):
                return gzip.open(p, "rt", encoding="utf-8"), cand.endswith(".building")
            return open(p, encoding="utf-8"), cand.endswith(".building")
    return None, False


def read_meta(data_dir):
    for cand in ("fetch_meta.json.building", "fetch_meta.json"):
        p = os.path.join(data_dir, cand)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return json.load(fh), cand.endswith(".building")
    return {}, False


def lock(data_dir):
    import fcntl
    fh = open(os.path.join(data_dir, "danbooru.lock"), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("別の fetch/build が実行中です（ロック取得失敗）", file=sys.stderr)
        sys.exit(1)
    return fh


def main():
    ap = argparse.ArgumentParser(description="danbooru.db を構築する")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--db", default="danbooru.db")
    ap.add_argument("--min-ratio", type=float, default=0.95,
                    help="前回件数に対する下限比（これ未満なら差し替えない）")
    args = ap.parse_args()
    os.makedirs(args.data_dir, exist_ok=True)
    _lock = lock(args.data_dir)

    building = args.db + ".building"
    if os.path.exists(building):
        os.remove(building)
    con = sqlite3.connect(building)
    con.executescript("""
    CREATE TABLE wiki(id INTEGER PRIMARY KEY, title TEXT, normalized_title TEXT,
      body TEXT, body_clean TEXT, see_also TEXT, is_deleted INTEGER,
      updated_at TEXT, source TEXT);
    CREATE INDEX idx_wiki_norm ON wiki(normalized_title);
    CREATE TABLE wiki_other_names(id INTEGER PRIMARY KEY, wiki_id INTEGER,
      name TEXT, normalized_name TEXT);
    CREATE INDEX idx_names_norm ON wiki_other_names(normalized_name);
    CREATE INDEX idx_names_name ON wiki_other_names(name);
    CREATE TABLE tags(name TEXT PRIMARY KEY, category INTEGER,
      post_count INTEGER, is_deprecated INTEGER, source TEXT);
    CREATE TABLE tag_aliases(antecedent_name TEXT, consequent_name TEXT, status TEXT);
    CREATE TABLE tag_implications(antecedent_name TEXT, consequent_name TEXT, status TEXT);
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE VIRTUAL TABLE wiki_fts USING fts5(title, body_clean,
      content='wiki', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
    """)

    counts = {}
    fh, fresh = open_input(args.data_dir, "wiki_pages")
    if fh is None:
        print("wiki_pages の入力がありません。先に fetch.py を実行してください",
              file=sys.stderr)
        sys.exit(1)
    n = 0
    with fh:
        for line in fh:
            r = json.loads(line)
            body = r.get("body") or ""
            clean, see_also = clean_body(body)
            con.execute(
                "INSERT OR REPLACE INTO wiki VALUES (?,?,?,?,?,?,?,?,?)",
                (r["id"], r["title"], normalize(r["title"]), body, clean,
                 json.dumps(see_also, ensure_ascii=False),
                 1 if r.get("is_deleted") else 0, r.get("updated_at"), "danbooru"))
            for nm in (r.get("other_names") or []):
                con.execute("INSERT INTO wiki_other_names(wiki_id,name,normalized_name)"
                            " VALUES (?,?,?)", (r["id"], nm, normalize(nm)))
            n += 1
    counts["wiki"] = n
    counts["wiki_other_names"] = con.execute(
        "SELECT COUNT(*) FROM wiki_other_names").fetchone()[0]
    # FTS は本文か別名のあるページだけを対象にする
    con.execute("""INSERT INTO wiki_fts(rowid, title, body_clean)
      SELECT id, title, body_clean FROM wiki
      WHERE (body IS NOT NULL AND body <> '')
         OR id IN (SELECT wiki_id FROM wiki_other_names)""")

    for name, table in (("tags", "tags"), ("tag_aliases", "tag_aliases"),
                        ("tag_implications", "tag_implications")):
        fh, _ = open_input(args.data_dir, name)
        cnt = 0
        if fh is not None:
            with fh:
                for line in fh:
                    r = json.loads(line)
                    if table == "tags":
                        con.execute("INSERT OR REPLACE INTO tags VALUES (?,?,?,?,?)",
                                    (r["name"], r.get("category"), r.get("post_count"),
                                     1 if r.get("is_deprecated") else 0, "danbooru"))
                    else:
                        con.execute(f"INSERT INTO {table} VALUES (?,?,?)",
                                    (r["antecedent_name"], r["consequent_name"],
                                     r.get("status", "active")))
                    cnt += 1
        counts[table] = cnt

    # 前回 DB の件数（検査と status の増減表示用）
    prev = {}
    if os.path.exists(args.db):
        old = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        try:
            prev = {k: int(v) for k, v in old.execute(
                "SELECT key, value FROM meta WHERE key LIKE 'count_%'")}
        except sqlite3.Error:
            prev = {}
        old.close()
    for table in ("wiki", "tags", "tag_aliases", "tag_implications"):
        if prev.get(f"count_{table}", 0) > 0 and \
                counts[table] < prev[f"count_{table}"] * args.min_ratio:
            print(f"件数検査に失敗: {table} {counts[table]} < "
                  f"前回 {prev[f'count_{table}']} の {args.min_ratio:.0%}",
                  file=sys.stderr)
            sys.exit(1)

    fetch_meta, _ = read_meta(args.data_dir)
    built_at = now_iso()
    meta = {"schema_version": str(SCHEMA_VERSION), "built_at": built_at,
            "snapshot_date": (fetch_meta.get("fetch_finished_at") or built_at)[:10],
            "fetch_started_at": fetch_meta.get("fetch_started_at", ""),
            "fetch_finished_at": fetch_meta.get("fetch_finished_at", ""),
            "max_id": str(con.execute("SELECT MAX(id) FROM wiki").fetchone()[0] or 0),
            "complete": "1"}
    for table, cnt in counts.items():
        meta[f"count_{table}"] = str(cnt)
        if f"count_{table}" in prev:
            meta[f"prev_count_{table}"] = str(prev[f"count_{table}"])
    con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    con.commit()

    qc = con.execute("PRAGMA quick_check").fetchone()[0]
    if qc != "ok":
        print(f"quick_check 失敗: {qc}", file=sys.stderr)
        sys.exit(1)
    con.execute("INSERT INTO wiki_fts(wiki_fts) VALUES('integrity-check')")
    con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    con.commit()
    con.close()

    for attempt in range(5):  # Windows で検索中プロセスが掴む場合の再試行
        try:
            os.replace(building, args.db)
            break
        except OSError:
            if attempt == 4:
                raise
            time.sleep(1)
    # 取得データの .building を正本へ昇格（DB 差し替え成功後に限る）
    for name in ("wiki_pages", "tags", "tag_aliases", "tag_implications"):
        src = os.path.join(args.data_dir, name + ".jsonl.gz.building")
        if os.path.exists(src):
            os.replace(src, os.path.join(args.data_dir, name + ".jsonl.gz"))
    src = os.path.join(args.data_dir, "fetch_meta.json.building")
    if os.path.exists(src):
        os.replace(src, os.path.join(args.data_dir, "fetch_meta.json"))
    print("BUILD_DONE", json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
