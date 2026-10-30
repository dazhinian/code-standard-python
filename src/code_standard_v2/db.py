"""SQLite 查询层：把卡片落成可查询的索引，并检测索引是否过期。

@stable
@calls: -
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

from .model import Card

DB_RELATIVE = os.path.join(".code-standard", "index.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY, mtime REAL NOT NULL, size INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY, file TEXT NOT NULL, qname TEXT NOT NULL,
    name TEXT NOT NULL, kind TEXT NOT NULL, line INTEGER NOT NULL,
    signature TEXT NOT NULL, summary TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tags (
    symbol_id INTEGER NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calls (
    caller_id INTEGER NOT NULL, callee_id INTEGER NOT NULL,
    name TEXT NOT NULL, line INTEGER NOT NULL, confidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS refs (
    caller_id INTEGER NOT NULL, callee_id INTEGER NOT NULL,
    name TEXT NOT NULL, line INTEGER NOT NULL, context TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS protocol (
    caller_id INTEGER NOT NULL, callee_id INTEGER NOT NULL,
    kind TEXT NOT NULL, line INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls(callee_id);
CREATE INDEX IF NOT EXISTS idx_refs_callee ON refs(callee_id);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
"""


def db_path(root: str) -> str:
    """返回索引数据库路径。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return os.path.join(root, DB_RELATIVE)


def build(cards: list[Card], files: list[str], root: str) -> str:
    """用卡片和文件清单重建 SQLite 索引。

    @effect: file.write, db.write
    @calls: db_path
    @raises: OSError
    @pre:
    @mutates:
    """
    path = db_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO files(path, mtime, size) VALUES (?, ?, ?)",
            [(relative, os.path.getmtime(os.path.join(root, relative)),
              os.path.getsize(os.path.join(root, relative)))
             for relative in files])
        ids: dict[tuple[str, str], int] = {}
        rows: list[tuple] = []
        for card in cards:
            cursor = conn.execute(
                "INSERT INTO symbols(file, qname, name, kind, line, signature, summary) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (card.file, card.qname, card.name, card.kind, card.start_line,
                 card.signature, card.summary))
            symbol_id = int(cursor.lastrowid)
            ids[(card.file, card.qname)] = symbol_id
            rows.append((symbol_id, card))
        conn.executemany(
            "INSERT INTO tags(symbol_id, name, value) VALUES (?, ?, ?)",
            [(symbol_id, tag, value)
             for symbol_id, card in rows
             for tag, values in card.tags.items()
             for value in (values or [""])])
        call_rows = []
        ref_rows = []
        proto_rows = []
        for caller_id, card in rows:
            for call in card.resolved_calls:
                target = call.get("target")
                if target is None:
                    continue
                callee_id = ids.get((target["file"], target["qname"]))
                if callee_id is None:
                    continue
                call_rows.append((caller_id, callee_id, call["name"],
                                  call["line"], call["confidence"]))
            for ref in card.resolved_references:
                target = ref.get("target")
                if target is None:
                    continue
                callee_id = ids.get((target["file"], target["qname"]))
                if callee_id is None:
                    continue
                ref_rows.append((caller_id, callee_id, ref["name"],
                                 ref["line"], ref["context"]))
            for item in card.protocol_calls:
                target = item.get("target")
                if target is None:
                    continue
                callee_id = ids.get((target["file"], target["qname"]))
                if callee_id is None:
                    continue
                proto_rows.append((caller_id, callee_id, item["kind"],
                                   item["line"]))
        conn.executemany(
            "INSERT INTO calls(caller_id, callee_id, name, line, confidence) "
            "VALUES (?, ?, ?, ?, ?)", call_rows)
        conn.executemany(
            "INSERT INTO refs(caller_id, callee_id, name, line, context) "
            "VALUES (?, ?, ?, ?, ?)", ref_rows)
        conn.executemany(
            "INSERT INTO protocol(caller_id, callee_id, kind, line) "
            "VALUES (?, ?, ?, ?)", proto_rows)
        conn.commit()
    finally:
        conn.close()
    return path


def stale_files(root: str) -> list[str]:
    """对比数据库里的文件状态与磁盘，返回过期/缺失/新增文件。

    @effect: file.read, db.read
    @calls: db_path
    @raises: sqlite3.Error
    @pre:
    @mutates:
    """
    path = db_path(root)
    if not os.path.exists(path):
        return ["<尚未建立索引>"]
    conn = sqlite3.connect(path)
    try:
        stored = {row[0]: (row[1], row[2]) for row in
                  conn.execute("SELECT path, mtime, size FROM files")}
    finally:
        conn.close()
    changed: list[str] = []
    for relative, (mtime, size) in stored.items():
        absolute = os.path.join(root, relative)
        if not os.path.exists(absolute):
            changed.append(relative + " (缺失)")
            continue
        if abs(os.path.getmtime(absolute) - mtime) > 1e-6 or \
                os.path.getsize(absolute) != size:
            changed.append(relative)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames
                       if name not in {".git", ".venv", "__pycache__",
                                       ".code-standard", "node_modules"}
                       and not name.startswith(".")]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            relative = os.path.relpath(os.path.join(dirpath, name), root)
            relative = relative.replace(os.sep, "/")
            if relative not in stored:
                changed.append(relative + " (新增)")
    return changed


def connect(root: str) -> sqlite3.Connection:
    """打开索引数据库。

    @effect: file.read
    @calls: db_path
    @raises: sqlite3.Error
    @pre:
    @mutates:
    """
    return sqlite3.connect(db_path(root))


def find(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    """按限定名或短名找符号。

    @effect: db.read
    @calls: -
    @raises: sqlite3.Error
    @pre:
    @mutates: conn
    """
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM symbols WHERE qname = ? OR name = ? "
        "ORDER BY file, line", (name, name)).fetchall()


def callers(conn: sqlite3.Connection, symbol_id: int) -> list[sqlite3.Row]:
    """谁调用了我（含协议方法）。

    @effect: db.read
    @calls: -
    @raises: sqlite3.Error
    @pre:
    @mutates: conn
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT s.qname, s.file, s.line, c.line AS call_line, c.confidence, "
        "'call' AS kind FROM calls c JOIN symbols s ON s.id = c.caller_id "
        "WHERE c.callee_id = ?", (symbol_id,)).fetchall()
    extra = conn.execute(
        "SELECT s.qname, s.file, s.line, p.line AS call_line, 'protocol' AS confidence, "
        "p.kind AS kind FROM protocol p JOIN symbols s ON s.id = p.caller_id "
        "WHERE p.callee_id = ?", (symbol_id,)).fetchall()
    return list(rows) + list(extra)


def callees(conn: sqlite3.Connection, symbol_id: int) -> list[sqlite3.Row]:
    """我调用了谁（含引用与协议方法）。

    @effect: db.read
    @calls: -
    @raises: sqlite3.Error
    @pre:
    @mutates: conn
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT s.qname, s.file, s.line, c.line AS call_line, c.confidence, "
        "'call' AS kind FROM calls c JOIN symbols s ON s.id = c.callee_id "
        "WHERE c.caller_id = ?", (symbol_id,)).fetchall()
    refs = conn.execute(
        "SELECT s.qname, s.file, s.line, r.line AS call_line, 'ref' AS confidence, "
        "r.context AS kind FROM refs r JOIN symbols s ON s.id = r.callee_id "
        "WHERE r.caller_id = ?", (symbol_id,)).fetchall()
    proto = conn.execute(
        "SELECT s.qname, s.file, s.line, p.line AS call_line, 'protocol' AS confidence, "
        "p.kind AS kind FROM protocol p JOIN symbols s ON s.id = p.callee_id "
        "WHERE p.caller_id = ?", (symbol_id,)).fetchall()
    return list(rows) + list(refs) + list(proto)


def impact(conn: sqlite3.Connection, symbol_id: int, depth: int = 2) -> list[tuple]:
    """反向影响面（调用者 BFS），返回 (depth, qname, file, line)。

    @effect: db.read
    @calls: callers, find
    @raises: sqlite3.Error
    @pre:
    @mutates:
    """
    seen = {symbol_id}
    frontier = [symbol_id]
    out: list[tuple] = []
    for level in range(1, depth + 1):
        nxt: list[int] = []
        for current in frontier:
            for row in callers(conn, current):
                qname = row["qname"]
                if qname in {item[1] for item in out}:
                    continue
                out.append((level, qname, row["file"], row["line"]))
                found = find(conn, qname)
                if found:
                    target_id = found[0]["id"]
                    if target_id not in seen:
                        seen.add(target_id)
                        nxt.append(target_id)
        frontier = nxt
    return out


def by_raises(conn: sqlite3.Connection, exception: str) -> list[sqlite3.Row]:
    """声明或直接抛某异常的符号。

    @effect: db.read
    @calls: -
    @raises: sqlite3.Error
    @pre:
    @mutates: conn
    """
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT s.* FROM symbols s JOIN tags t ON t.symbol_id = s.id "
        "WHERE t.name = 'raises' AND t.value LIKE ?", (f"%{exception}%",)).fetchall()


def by_effect(conn: sqlite3.Connection, kind: str) -> list[sqlite3.Row]:
    """声明某效果的符号。

    @effect: db.read
    @calls: -
    @raises: sqlite3.Error
    @pre:
    @mutates: conn
    """
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT s.* FROM symbols s JOIN tags t ON t.symbol_id = s.id "
        "WHERE t.name = 'effect' AND t.value LIKE ?", (f"%{kind}%",)).fetchall()


def describe(row: sqlite3.Row) -> str:
    """把符号行格式化成一行输出。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return (f"{row['qname']}  {row['file']}:{row['line']}  "
            f"[{row['kind']}] {row['summary']}".rstrip())


def any_row(value: Any) -> Any:
    """占位，避免未使用导入告警。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return value
