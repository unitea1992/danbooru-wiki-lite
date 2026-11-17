#!/usr/bin/env python3
"""ネットワークなしで動くテスト。架空のタグ/Wiki で小さな DB を作り、
検索の順序・JSON の形・エラーコードを確かめる（DESIGN 第5.2節）。

実行: python3 tests/test_search.py
"""
import json, os, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WIKI = [
    {"id": 1, "title": "maid", "body": "A maid is a domestic worker.\n\nh4. See also\n\n* [[maid_uniform]]\n* [[apron]]\n\nh4. Posts\n\npost listing here",
     "other_names": ["メイド", "メイドさん"], "is_deleted": False, "updated_at": "2026-01-01T00:00:00Z"},
    {"id": 2, "title": "maid_uniform", "body": "The uniform worn by a maid.",
     "other_names": ["メイド服"], "is_deleted": False, "updated_at": "2026-01-01T00:00:00Z"},
    {"id": 3, "title": "silver_hair", "body": "Hair that is silver in color. See [[grey_hair]].",
     "other_names": ["銀髪"], "is_deleted": False, "updated_at": "2026-01-01T00:00:00Z"},
    {"id": 4, "title": "empty_page", "body": "", "other_names": [],
     "is_deleted": False, "updated_at": "2026-01-01T00:00:00Z"},
]
TAGS = [
    {"id": 1, "name": "maid", "category": 0, "post_count": 50000, "is_deprecated": False},
    {"id": 2, "name": "maid_uniform", "category": 0, "post_count": 30000, "is_deprecated": False},
    {"id": 3, "name": "silver_hair", "category": 0, "post_count": 20000, "is_deprecated": False},
    {"id": 4, "name": "grey_hair", "category": 0, "post_count": 25000, "is_deprecated": False},
    {"id": 5, "name": "old_tag", "category": 0, "post_count": 3, "is_deprecated": True},
]
ALIASES = [{"antecedent_name": "grey_hair", "consequent_name": "silver_hair", "status": "active"}]
IMPLICATIONS = [{"antecedent_name": "maid_uniform", "consequent_name": "maid", "status": "active"},
                {"antecedent_name": "maid", "consequent_name": "apron", "status": "active"}]


def run(args, cwd, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([sys.executable] + args, cwd=cwd, env=env,
                          capture_output=True, text=True)


def request(db, payload):
    p = run([os.path.join(ROOT, "search.py"), "--request-json", json.dumps(payload)],
            cwd=ROOT, env_extra={"DANBOORU_DB": db})
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    return json.loads(p.stdout)


def main():
    tmp = tempfile.mkdtemp()
    data = os.path.join(tmp, "data")
    os.makedirs(data)
    for name, rows in (("wiki_pages", WIKI), ("tags", TAGS),
                       ("tag_aliases", ALIASES), ("tag_implications", IMPLICATIONS)):
        with open(os.path.join(data, name + ".jsonl"), "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    db = os.path.join(tmp, "danbooru.db")
    p = run([os.path.join(ROOT, "build.py"), "--data-dir", data, "--db", db], cwd=ROOT)
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert "BUILD_DONE" in p.stdout, p.stdout

    # status
    env = request(db, {"action": "status"})
    assert env["ok"] and env["results"][0]["counts"]["wiki"]["count"] == 4, env

    # search: exact が先、本文 FTS が後
    env = request(db, {"action": "search", "query": "maid"})
    tags = [r["tag"] for r in env["results"]]
    assert tags[0] == "maid" and env["results"][0]["match"] == "exact", env

    # 日本語名（other_names）で引ける
    env = request(db, {"action": "search", "query": "メイド"})
    assert any(r["tag"] == "maid" for r in env["results"]), env

    # alias 解決が先頭に出る
    env = request(db, {"action": "search", "query": "grey hair"})
    assert env["results"][0]["tag"] == "silver_hair", env
    assert env["results"][0]["match"] == "alias", env

    # resolve
    env = request(db, {"action": "resolve", "tag": "grey_hair"})
    assert env["results"][0]["canonical"] == "silver_hair", env

    # get: Posts 節が切れていること、see_also が取れること
    env = request(db, {"action": "get", "tag": "maid"})
    body = env["results"][0]["body"]
    assert "post listing" not in body and "domestic worker" in body, body
    env = request(db, {"action": "wiki-links", "tag": "maid"})
    assert env["results"][0]["see_also"] == ["maid_uniform", "apron"], env

    # implications: depth=2 で推移的に辿れる＋逆方向
    env = request(db, {"action": "implications", "tag": "maid_uniform", "depth": 2})
    r = env["results"][0]
    assert "maid" in r["implies"] and "apron" in r["implies"], r
    env = request(db, {"action": "implications", "tag": "maid"})
    assert "maid_uniform" in env["results"][0]["implied_by"], env

    # lookup: 見つからないものは found=false（エラーにしない）
    env = request(db, {"action": "lookup", "tags": ["maid", "no_such_tag"]})
    assert env["results"][0]["found"] and not env["results"][1]["found"], env

    # export-csv
    csv_path = os.path.join(tmp, "out.csv")
    env = request(db, {"action": "export-csv", "path": csv_path, "min_post_count": 100})
    assert env["results"][0]["rows"] == 4, env
    with open(csv_path, encoding="utf-8") as fh:
        assert "silver_hair,0,20000,grey_hair" in fh.read(), "alias 列"

    # 不明なキーは bad-request（終了コード2）
    p = run([os.path.join(ROOT, "search.py"), "--request-json",
             json.dumps({"action": "search", "query": "x", "bogus": 1})],
            cwd=ROOT, env_extra={"DANBOORU_DB": db})
    assert p.returncode == 2, (p.returncode, p.stdout)
    assert json.loads(p.stdout)["error"]["code"] == "bad-request"

    # DB が無い場合は db-missing（終了コード3）
    p = run([os.path.join(ROOT, "search.py"), "--request-json",
             json.dumps({"action": "status"})],
            cwd=ROOT, env_extra={"DANBOORU_DB": os.path.join(tmp, "none.db")})
    assert p.returncode == 3, (p.returncode, p.stdout)
    assert json.loads(p.stdout)["error"]["code"] == "db-missing"

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
