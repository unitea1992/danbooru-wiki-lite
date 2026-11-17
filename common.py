"""danbooru-wiki-lite 共通部品。正規化は構築・検索・resolve で必ずここを通す。"""
import re

SCHEMA_VERSION = 1


def normalize(name):
    """タグ名の正規化: 小文字化し、空白の並びを _ に寄せる。"""
    if name is None:
        return ""
    return re.sub(r"\s+", "_", str(name).strip().lower())


def now_iso():
    from datetime import datetime, timedelta, timezone
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).isoformat(timespec="seconds")
