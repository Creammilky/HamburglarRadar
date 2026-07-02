"""SQLite 连接与初始化。

用法：
    python -m src.storage.db init
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from src.config import REPO_ROOT, get_config

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _resolve_path(sqlite_path: str) -> Path:
    p = Path(sqlite_path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p


def get_connection(sqlite_path: str | None = None) -> sqlite3.Connection:
    if sqlite_path is None:
        sqlite_path = get_config().env.sqlite_path
    path = _resolve_path(sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db(sqlite_path: str | None = None) -> Path:
    if sqlite_path is None:
        sqlite_path = get_config().env.sqlite_path
    path = _resolve_path(sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(sqlite_path)
    try:
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.executescript(sql_file.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    return path


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "init":
        path = init_db()
        print(f"[db] initialized SQLite at {path}")
        return 0
    print("usage: python -m src.storage.db init")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
