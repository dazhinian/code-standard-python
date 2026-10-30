"""命令行：sync 生成卡片、check 对账、brief 组装生成上下文。

@stable
@calls: -
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import re
import subprocess
import sys
from importlib import resources

from . import db as index_db
from .check import Finding, check_project
from .config import Config
from .config import load as load_config
from .config import read_agents
from .config import save as save_config
from .config import write_agents
from .extract import SourceError, extract_file
from .model import Card, ImportFact
from .resolve import build_project, resolve_project
from .rules import RULES
from .rules import as_json as rules_json
from .rules import as_markdown as rules_markdown
from .rules import as_text as rules_text
from .writeback import update_source, write_source

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".code-standard", ".code-index",
    ".venv", "venv", "env", ".tox", "__pycache__",
    "node_modules", "build", "dist", ".mypy_cache", ".pytest_cache",
    "tests",
}


def find_root(start: str) -> str:
    """向上找最近的 .git；找不到就用起始目录。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    current = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.abspath(start)
        current = parent


def iter_py(root: str, extra_skip: set[str] | None = None) -> list[str]:
    """枚举项目内 .py 文件，跳过依赖/产物/隐藏目录与配置指定目录。

    @effect: file.read
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    skip = SKIP_DIRS | (extra_skip or set())
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in skip and not name.startswith("."))
        for name in sorted(filenames):
            if name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
    return out


def analyze(root: str, config: Config | None = None,
            use_cache: bool = True) -> tuple[list[Card], dict, list[Finding]]:
    """抽取整仓库卡片并解析调用边；未显式传配置时读取项目配置。

    @effect: file.read, file.write, state
    @calls: config.load, iter_py, check.Finding, extract.extract_file, resolve.build_project, resolve.resolve_project
    @raises: -
    @pre:
    @mutates:
    """
    if config is None:
        config, _note = load_config(root)
    cache_path = os.path.join(root, ".code-standard", "cache.pkl")
    cache: dict = {"version": 3, "files": {}}
    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as handle:
                loaded = pickle.load(handle)
            if loaded.get("version") == 3:
                cache = loaded
        except (OSError, pickle.PickleError, EOFError, AttributeError):
            cache = {"version": 3, "files": {}}
    cards: list[Card] = []
    imports: dict[str, list[ImportFact]] = {}
    errors: list[Finding] = []
    for path in iter_py(root, set(config.skip_dirs)):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        try:
            stat = os.stat(path)
        except OSError as exc:
            errors.append(Finding(rel, 1, "<file>", "C0", "error", str(exc),
                                  "检查文件权限"))
            continue
        hit = cache["files"].get(rel)
        if (hit is not None and hit["mtime"] == stat.st_mtime
                and hit["size"] == stat.st_size):
            cards.extend(hit["cards"])
            imports[rel] = hit["imports"]
            continue
        try:
            facts = extract_file(path, rel)
        except SourceError as exc:
            errors.append(Finding(rel, 1, "<file>", "C0", "error", str(exc),
                                  "修好编码/语法后重新抽取"))
            cache["files"].pop(rel, None)
            continue
        cards.extend(facts.cards)
        imports[rel] = facts.imports
        cache["files"][rel] = {"mtime": stat.st_mtime, "size": stat.st_size,
                               "cards": facts.cards, "imports": facts.imports}
    if use_cache:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        cache["files"] = {key: value for key, value in cache["files"].items()
                          if os.path.exists(os.path.join(root, key))}
        temp_path = cache_path + ".tmp"
        with open(temp_path, "wb") as handle:
            pickle.dump(cache, handle)
        os.replace(temp_path, cache_path)
    project = build_project(cards, imports)
    stats = resolve_project(project)
    return cards, stats, errors


def summarize(cards: list[Card], stats: dict) -> dict:
    """汇总卡片规模与解析置信度分布。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    kinds: dict[str, int] = {}
    purities: dict[str, int] = {}
    for card in cards:
        kinds[card.kind] = kinds.get(card.kind, 0) + 1
        if card.kind in ("function", "method"):
            pure = "pure" if "pure" in card.tags else (
                "impure" if "side-effect" in card.tags else "undeclared")
            purities[pure] = purities.get(pure, 0) + 1
    total = stats.get("calls_total", 0)
    resolved = stats.get("resolved", 0)
    return {
        "files": kinds.get("module", 0),
        "classes": kinds.get("class", 0),
        "functions": kinds.get("function", 0),
        "methods": kinds.get("method", 0),
        "calls_total": total,
        "calls_resolved": resolved,
        "resolution_rate": round(resolved / total, 4) if total else 0.0,
        "by_confidence": stats.get("by_confidence", {}),
        "purity": purities,
    }


def _write_atomic(path: str, text: str) -> None:
    """原子写文件（同目录临时文件 + replace）。

    @effect: file.write
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temp, path)


def write_artifacts(root: str, cards: list[Card], summary: dict) -> str:
    """把 cards.jsonl / summary.json / .gitignore 写到 .code-standard/。

    @effect: file.write
    @calls: _write_atomic
    @raises: -
    @pre:
    @mutates:
    """
    out_dir = os.path.join(root, ".code-standard")
    lines = []
    for card in sorted(cards, key=lambda item: (item.file, item.start_line, item.kind)):
        lines.append(json.dumps(card.to_dict(), ensure_ascii=False))
    _write_atomic(os.path.join(out_dir, "cards.jsonl"), "\n".join(lines) + "\n")
    _write_atomic(os.path.join(out_dir, "summary.json"),
                  json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    ignore = os.path.join(out_dir, ".gitignore")
    if not os.path.exists(ignore):
        _write_atomic(ignore, "*\n")
    return out_dir


def _read_data(name: str) -> str:
    """读取随包发布的文本文件。

    @effect: file.read
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    try:
        return resources.files("code_standard_v2").joinpath(
            "data", name).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        fallback = os.path.join(os.path.dirname(__file__), "data", name)
        with open(fallback, encoding="utf-8") as handle:
            return handle.read()


def read_prompt() -> str:
    """读取生成提示（随包发布，一份通用模板，含修改场景的补充规则）。

    @effect: file.read
    @calls: _read_data
    @raises: -
    @pre:
    @mutates:
    """
    return _read_data("prompt.md").rstrip() + "\n"


def _git(root: str, *args: str) -> str | None:
    """在项目根执行 git，失败返回 None。

    @effect: process
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    try:
        done = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True, text=True, check=False)
    except OSError:
        return None
    if done.returncode != 0:
        return None
    return done.stdout


def _split_z(text: str) -> set[str]:
    """解析 `-z` 输出的 NUL 分隔路径。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return {item for item in text.split("\0") if item}


def changed_files(root: str, staged: bool = False) -> set[str] | None:
    """返回相对项目根的改动文件集合；非 git 仓库返回 None。

    @effect: process
    @calls: _git, _split_z
    @raises: -
    @pre:
    @mutates:
    """
    if staged:
        text = _git(root, "diff", "--cached", "--name-only",
                    "--diff-filter=ACMR", "-z")
        return None if text is None else _split_z(text)
    tracked = _git(root, "diff", "--name-only", "-z", "HEAD")
    if tracked is None:
        return None
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z") or ""
    return _split_z(tracked) | _split_z(untracked)


def _parse_mypy_output(text: str, root: str) -> list[Finding]:
    """把 mypy 的文本输出转成 findings（T1）。

    @pure
    @calls: check.Finding
    @raises: -
    @pre:
    @mutates:
    """
    pattern = re.compile(
        r"^(?P<file>.+?):(?P<line>\d+): "
        r"(?P<severity>error|warning|note): (?P<message>.*?)(?: \[(?P<code>[\w-]+)\])?$")
    findings: list[Finding] = []
    for line in text.splitlines():
        match = pattern.match(line)
        if match is None or match.group("severity") == "note":
            continue
        path = match.group("file")
        if os.path.isabs(path):
            path = os.path.relpath(path, root).replace(os.sep, "/")
        message = match.group("message")
        code = match.group("code")
        if code:
            message = f"{message} [{code}]"
        findings.append(Finding(
            path, int(match.group("line")), "<type>", "T1",
            "error" if match.group("severity") == "error" else "warning",
            message, "修正类型或注解（mypy）"))
    return findings


def _run_mypy(root: str) -> list[Finding]:
    """调用 mypy 做类型检查；未安装或崩溃时返回 T0 error。

    @effect: process
    @calls: check.Finding, _parse_mypy_output
    @raises: -
    @pre:
    @mutates:
    """
    command = [sys.executable, "-m", "mypy", "--show-error-codes",
               "--no-error-summary", "--no-color-output", "--hide-error-context",
               root]
    try:
        done = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        return [Finding(root, 1, "<mypy>", "T0", "error",
                        f"无法运行 mypy: {exc}", "安装 mypy 或去掉 --types")]
    if done.returncode not in (0, 1):
        detail = (done.stderr or done.stdout).strip().splitlines()
        message = detail[-1] if detail else f"mypy 退出码 {done.returncode}"
        return [Finding(root, 1, "<mypy>", "T0", "error",
                        f"mypy 运行失败: {message}", "检查 mypy 配置")]
    return _parse_mypy_output(done.stdout, root)


def _report(findings: list[Finding], as_json: bool) -> str:
    """格式化 findings。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    if as_json:
        return json.dumps([item.__dict__ for item in findings],
                          ensure_ascii=False, indent=2)
    if not findings:
        return "[code-standard-v2] 通过：声明与事实一致。"
    lines: list[str] = []
    for item in findings:
        lines.append(f"{item.file}:{item.line}  {item.symbol}  "
                     f"{item.rule} {item.severity}  {item.message}")
        lines.append(f"    修复: {item.fix}")
    errors = sum(1 for item in findings if item.severity == "error")
    warnings = len(findings) - errors
    lines.append(f"汇总: {len(findings)} 条（error {errors}, warning {warnings}）")
    return "\n".join(lines)


def _render_card(card: Card, with_calls: bool = True) -> list[str]:
    """把一张卡渲染成 brief 里的紧凑行（含标签值、字段、调用、调用者）。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    if card.kind in ("function", "method"):
        head = f"- {card.qname}  {card.signature}"
    else:
        head = f"- [{card.kind}] {card.qname}"
    if card.summary:
        head += f"   # {card.summary}"
    lines = [head]

    if card.kind == "class" and card.fields:
        for field in card.fields[:10]:
            text = f"{field['name']}: {field['type'] or '?'}"
            if field["default"] is not None:
                text += f" = {field['default']}"
            if field["classvar"]:
                text += " (ClassVar)"
            lines.append(f"    · {text}")

    for name, values in card.tags.items():
        if name == "calls":
            continue
        cleaned = [item for item in values if item.strip()]
        if name in ("pure", "thread-safe", "not-thread-safe", "idempotent",
                    "transactional", "stable", "experimental", "entry"):
            lines.append(f"    @{name}")
        elif cleaned:
            lines.append(f"    @{name}: {'; '.join(cleaned)[:100]}")
        else:
            lines.append(f"    @{name}: （空）")

    if card.kind == "module" and card.exports:
        lines.append(f"    __all__: {', '.join(card.exports[:10])}")
    if card.entry_decorators:
        lines.append(f"    entry: {', '.join(card.entry_decorators[:3])}")

    direct = {item.type for item in card.raises if item.type}
    extra = [name for name in card.propagated_raises if name not in direct]
    if extra:
        lines.append(f"    下游可能传播: {', '.join(extra[:8])}")

    if with_calls:
        internal = [call for call in card.resolved_calls
                    if call.get("confidence") in ("exact", "inferred", "ambiguous")]
        for call in internal[:6]:
            target = call.get("target")
            if target:
                lines.append(f"    → {target['qname']} ({call['confidence']}) "
                             f"{target['file']}:{target['line']}")
            else:
                lines.append(f"    → {call['name']} ({call['confidence']})")
        hidden = len(card.resolved_calls) - len(internal)
        if hidden:
            lines.append(f"    … 另有 {hidden} 个外部/未解析调用（卡片里有完整记录）")
        for caller in card.called_by[:5]:
            lines.append(f"    ← {caller['qname']} ({caller['confidence']}) "
                         f"{caller['file']}:{caller['line']}")
        for ref in card.resolved_references[:6]:
            target = ref["target"]
            lines.append(f"    ref→ {target['qname']} ({ref['confidence']}) "
                         f"{ref['context']} {target['file']}:{target['line']}")
        for item in card.referenced_by[:6]:
            lines.append(f"    ref← {item['qname']} ({item['confidence']}) "
                         f"{item['context']} {item['file']}:{item['line']}")
        for item in card.protocol_calls[:6]:
            target = item["target"]
            lines.append(f"    proto→ {target['qname']} ({item['kind']}) "
                         f"{target['file']}:{target['line']}")
        for item in card.protocol_called_by[:6]:
            lines.append(f"    proto← {item['qname']} ({item['kind']}) "
                         f"{item['file']}:{item['line']}")
    return lines


def _resolve_file(root: str, pattern: str) -> str:
    """把用户给的路径/片段规范成仓库相对路径。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    text = pattern.replace(os.sep, "/")
    if os.path.isabs(pattern):
        text = os.path.relpath(pattern, root).replace(os.sep, "/")
    return text


def _brief_context(cards: list[Card], args: argparse.Namespace,
                   root: str) -> list[str]:
    """生成 brief 的“当前事实”部分。

    @pure
    @calls: _resolve_file, _render_card
    @raises: -
    @pre:
    @mutates:
    """
    lines: list[str] = []
    selected: list[Card] = []
    if args.file:
        target = _resolve_file(root, args.file)
        selected = [card for card in cards
                    if card.file == target or card.file.endswith("/" + target)]
        if selected:
            lines.append(f"## 目标文件 `{selected[0].file}` 的现有符号")
    elif args.symbol:
        patterns = set(args.symbol)
        selected = [card for card in cards
                    if card.kind != "module"
                    and (card.qname in patterns or card.name in patterns)]
        if selected:
            lines.append(f"## 目标符号 `{', '.join(args.symbol)}`")
    if selected and args.depth:
        index = {(card.file, card.qname): card for card in cards
                 if card.kind != "module"}
        seen = {(card.file, card.qname) for card in selected}
        frontier = list(selected)
        for _ in range(args.depth):
            nxt: list[Card] = []
            for card in frontier:
                keys: list[tuple[str, str]] = []
                for call in card.resolved_calls:
                    target = call.get("target")
                    if target:
                        keys.append((target["file"], target["qname"]))
                for caller in card.called_by:
                    keys.append((caller["file"], caller["qname"]))
                for key in keys:
                    if key in seen:
                        continue
                    hit = index.get(key)
                    if hit is not None:
                        seen.add(key)
                        nxt.append(hit)
            selected.extend(nxt)
            frontier = nxt
            if not frontier:
                break
        lines.append(f"## 邻接范围：{args.depth} 跳，共 {len(selected)} 张卡")
    if not selected:
        counts: dict[str, int] = {}
        for card in cards:
            counts[card.kind] = counts.get(card.kind, 0) + 1
        lines.append("## 项目规模")
        lines.append(f"- 模块 {counts.get('module', 0)}，类 {counts.get('class', 0)}，"
                     f"函数 {counts.get('function', 0)}，方法 {counts.get('method', 0)}")
        lines.append("- 需要具体符号时用 `--file <路径>` 或 `--symbol <名字>` 重新生成 brief")
        return lines
    for card in selected[:12]:
        lines.extend(_render_card(card))
    return lines


def cmd_sync(args: argparse.Namespace) -> int:
    """生成卡片；`--write` 同时把 @calls 写回 docstring。

    @effect: file.write, log, file.read, state
    @calls: find_root, config.load, analyze, summarize, write_artifacts, writeback.update_source, writeback.write_source
    @raises: -
    @pre:
    @mutates:
    """
    root = os.path.abspath(args.root or find_root(os.getcwd()))
    config, note = load_config(root)
    if note:
        print(f"[csp] {note}", file=sys.stderr)
    cards, stats, errors = analyze(root, config)
    summary = summarize(cards, stats)
    summary["errors"] = [item.__dict__ for item in errors]
    out_dir = write_artifacts(root, cards, summary)

    written_files = 0
    written_funcs = 0
    if args.write:
        by_file: dict[str, list[Card]] = {}
        for card in cards:
            by_file.setdefault(card.file, []).append(card)
        for rel, items in sorted(by_file.items()):
            path = os.path.join(root, rel.replace("/", os.sep))
            try:
                new_text, changed = update_source(path, items)
            except (OSError, SyntaxError) as exc:
                print(f"[csp] 跳过 {rel}: {exc}", file=sys.stderr)
                continue
            if changed:
                write_source(path, new_text)
                written_files += 1
                written_funcs += changed

    if not args.quiet:
        print(f"[csp] {summary['files']} 模块, {summary['classes']} 类, "
              f"{summary['functions']} 函数, {summary['methods']} 方法, "
              f"调用 {summary['calls_resolved']}/{summary['calls_total']} 已解析 "
              f"({summary['resolution_rate']:.1%}) -> {out_dir}", file=sys.stderr)
        if args.write:
            print(f"[csp] 已写回 @calls: {written_funcs} 个函数 / {written_files} 个文件",
                  file=sys.stderr)
        for item in errors:
            print(f"[csp] {item.file}: {item.message}", file=sys.stderr)
    return 1 if errors else 0


def cmd_check(args: argparse.Namespace) -> int:
    """对账并输出结果。

    @effect: log, file.read, file.write, process, state
    @calls: find_root, config.load, analyze, check.check_project, _run_mypy, changed_files, _report
    @raises: -
    @pre:
    @mutates:
    """
    root = os.path.abspath(args.root or find_root(os.getcwd()))
    config, note = load_config(root)
    if note:
        print(f"[csp] {note}", file=sys.stderr)
    strict = args.strict or config.strict
    cards, _stats, source_errors = analyze(
        root, config, use_cache=not args.no_cache)
    findings = check_project(cards, strict=strict)
    findings.extend(source_errors)
    if args.types:
        findings.extend(_run_mypy(root))

    wanted: set[str] | None = None
    if args.staged or args.changed:
        changed = changed_files(root, staged=args.staged)
        if changed is None:
            print("[csp] 不是 git 仓库或 git 不可用；--changed/--staged 无法使用",
                  file=sys.stderr)
            return 2
        wanted = changed
        if not any(path.endswith(".py") for path in wanted):
            print("[csp] 没有改动的 Python 文件。", file=sys.stderr)
            return 0
    elif args.paths:
        wanted = {path.replace(os.sep, "/") for path in args.paths}

    if wanted is not None:
        findings = [item for item in findings
                    if item.file in wanted
                    or any(item.file.endswith("/" + path) for path in wanted)]
    print(_report(findings, args.json))
    return 1 if any(item.severity == "error" for item in findings) else 0


def cmd_db(args: argparse.Namespace) -> int:
    """重建或增量同步 SQLite 索引。

    @effect: file.write, db.write, log, file.read, db.read, state
    @calls: find_root, db.stale_files, config.load, analyze, iter_py, db.build
    @raises: -
    @pre:
    @mutates:
    """
    root = os.path.abspath(args.root or find_root(os.getcwd()))
    if getattr(args, "sync", False):
        stale = index_db.stale_files(root)
        if not stale:
            print("[csp] 索引已是最新，无需重建")
            return 0
    config, _note = load_config(root)
    cards, _stats, errors = analyze(root, config)
    if errors:
        print(f"[csp] 源码错误 {len(errors)} 个，索引可能不完整", file=sys.stderr)
    files = [os.path.relpath(path, root).replace(os.sep, "/")
             for path in iter_py(root, set(config.skip_dirs))]
    path = index_db.build(cards, files, root)
    print(f"[csp] 索引 {len(cards)} 个符号 / {len(files)} 个文件 -> {path}")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    """查询 SQLite 索引（调用者/被调/影响面/异常/效果）。

    @effect: db.read, log, file.read
    @calls: find_root, db.stale_files, db.connect, db.by_raises, db.by_effect, db.describe, db.find, db.callers, db.callees, db.impact
    @raises: -
    @pre:
    @mutates:
    """
    root = os.path.abspath(args.root or find_root(os.getcwd()))
    stale = index_db.stale_files(root)
    if stale:
        print(f"[csp] 索引过期（{len(stale)} 个文件），请先 csp db build", file=sys.stderr)
    conn = index_db.connect(root)
    try:
        if args.what in ("raises", "effects"):
            rows = (index_db.by_raises(conn, args.name)
                    if args.what == "raises"
                    else index_db.by_effect(conn, args.name))
            for row in rows:
                print(index_db.describe(row))
            print(f"[csp] {len(rows)} 条")
            return 0
        symbols = index_db.find(conn, args.name)
        if not symbols:
            print(f"[csp] 找不到符号: {args.name}", file=sys.stderr)
            return 1
        if args.what == "symbol":
            for row in symbols:
                print(index_db.describe(row))
            return 0
        symbol = symbols[0]
        if args.what == "callers":
            rows = index_db.callers(conn, symbol["id"])
            for row in rows:
                print(f"{row['qname']}  {row['file']}:{row['call_line']}  "
                      f"[{row['kind']}/{row['confidence']}]")
            print(f"[csp] {len(rows)} 个调用者")
        elif args.what == "callees":
            rows = index_db.callees(conn, symbol["id"])
            for row in rows:
                print(f"{row['qname']}  {row['file']}:{row['call_line']}  "
                      f"[{row['kind']}/{row['confidence']}]")
            print(f"[csp] {len(rows)} 个被调")
        else:
            rows = index_db.impact(conn, symbol["id"], args.depth)
            for level, qname, file, line in rows:
                print(f"{'  ' * (level - 1)}{qname}  {file}:{line}  (depth {level})")
            print(f"[csp] 影响面 {len(rows)} 个")
        return 0
    finally:
        conn.close()


def cmd_skill_install(args: argparse.Namespace) -> int:
    """把内置 SKILL 安装到用户 skills 目录。

    @effect: file.write, log
    @calls: -
    @raises: OSError
    @pre:
    @mutates:
    """
    import shutil

    source = resources.files("code_standard_v2").joinpath(
        "data", "skill", "code-standard-python")
    dest_root = os.path.abspath(os.path.expanduser(args.dest))
    os.makedirs(dest_root, exist_ok=True)
    target = os.path.join(dest_root, "code-standard-python")
    if os.path.exists(target):
        shutil.rmtree(target)
    shutil.copytree(str(source), target)
    print(f"[csp] 已安装 SKILL -> {target}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """打印卡片规模与解析统计。

    @effect: log, file.read, file.write, state
    @calls: find_root, config.load, analyze, summarize
    @raises: -
    @pre:
    @mutates:
    """
    root = os.path.abspath(args.root or find_root(os.getcwd()))
    config, _note = load_config(root)
    cards, stats, errors = analyze(root, config)
    summary = summarize(cards, stats)
    summary["source_errors"] = [item.message for item in errors]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    """打印生成提示（随包发布，避免 skill 里的副本过期）。

    @effect: log, file.read
    @calls: read_prompt
    @raises: -
    @pre:
    @mutates:
    """
    print(read_prompt(), end="")
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    """打印规则表。

    @effect: log
    @calls: rules.as_json, rules.as_markdown, rules.as_text
    @raises: -
    @pre:
    @mutates:
    """
    if args.json:
        print(rules_json())
    elif args.markdown:
        print(rules_markdown())
    else:
        print(rules_text())
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    """组装“生成上下文”：规则提示 + 项目策略 + 目标符号的现有事实。

    @effect: log, file.read, file.write, state
    @calls: find_root, config.load, analyze, summarize, read_prompt, config.read_agents, _brief_context
    @raises: -
    @pre:
    @mutates:
    """
    root = os.path.abspath(args.root or find_root(os.getcwd()))
    config, note = load_config(root)
    if note:
        print(f"[csp] {note}", file=sys.stderr)
    cards, stats, errors = analyze(
        root, config, use_cache=not args.no_cache)
    summary = summarize(cards, stats)

    lines: list[str] = []
    if not args.no_prompt:
        lines.append(read_prompt().rstrip())
        lines.append("")
        lines.append("---")
        lines.append("")
    lines.append("# 项目上下文")
    lines.append("")
    lines.append(f"- 根目录：`{root}`")
    lines.append(f"- 策略：strict={config.strict}, "
                 f"skip_dirs={config.skip_dirs or '[]'}")
    lines.append(f"- 规模：{summary['files']} 模块 / {summary['classes']} 类 / "
                 f"{summary['functions']} 函数 / {summary['methods']} 方法 / "
                 f"调用解析率 {summary['resolution_rate']:.1%}")
    if errors:
        lines.append(f"- 注意：{len(errors)} 个文件无法解析（C0），先修它们")
    agents = read_agents(root)
    if agents:
        lines.append("")
        lines.append("## 项目约定（来自 AGENTS.md）")
        lines.append("")
        lines.append(agents)
    lines.append("")
    lines.extend(_brief_context(cards, args, root))
    lines.append("")
    lines.append("## 验收")
    lines.append("")
    if args.file:
        lines.append(f"- 改完运行：`csp check --root . --paths {args.file}`")
    else:
        lines.append("- 改完运行：`csp check --root . --changed`")
    lines.append("- 0 error 才算完成；需要刷新卡片时运行 `csp sync --root .`")
    text = "\n".join(lines).rstrip() + "\n"
    print(text, end="")
    print(f"[csp] brief 约 {len(text) // 4} tokens", file=sys.stderr)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """初始化项目策略：写 .code-standard.json 与 AGENTS.md。

    @effect: file.write, log, file.read
    @calls: config.Config, config.save, config.write_agents
    @raises: -
    @pre:
    @mutates:
    """
    root = os.path.abspath(args.root or os.getcwd())
    path = os.path.join(root, ".code-standard.json")
    if os.path.exists(path) and not args.force:
        print(f"[csp] 已存在 {path}；如需覆盖加 --force", file=sys.stderr)
        return 2
    config = Config(strict=args.strict, skip_dirs=args.skip_dirs or [])
    written = save_config(root, config)
    agents, added = write_agents(root)
    print(f"[csp] 已写入 {written}")
    print(f"[csp] {'已追加' if added else '已存在'}规范说明：{agents}")
    print("[csp] 下一步：csp brief --file <目标文件>；提交前 csp check --staged --strict")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    parser = argparse.ArgumentParser(
        prog="csp", description="code-standard v2：声明与事实对账")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sync = sub.add_parser("sync", help="从源码生成卡片（--write 同时写回 @calls）")
    sync.add_argument("--root", help="项目根（默认：最近的 .git）")
    sync.add_argument("--write", action="store_true",
                      help="把生成的 @calls 写回 docstring（只改这一行）")
    sync.add_argument("--quiet", action="store_true")
    sync.set_defaults(func=cmd_sync)

    check = sub.add_parser("check", help="校验声明与事实是否一致")
    check.add_argument("paths", nargs="*", help="只报告这些文件（相对项目根）")
    check.add_argument("--root", help="项目根（默认：最近的 .git）")
    check.add_argument("--json", action="store_true", help="输出 JSON")
    check.add_argument("--strict", action="store_true",
                       help="warning 升级为 error；模块必须声明稳定性")
    check.add_argument("--changed", action="store_true",
                       help="只检查相对 HEAD 的改动（含未跟踪文件）")
    check.add_argument("--staged", action="store_true",
                       help="只检查已暂存文件（pre-commit 用）")
    check.add_argument("--types", action="store_true",
                       help="额外运行 mypy，把类型错误并入 T1（需已安装 mypy）")
    check.add_argument("--no-cache", action="store_true",
                       help="不读写 .code-standard/cache.pkl（只读 CI 用）")
    check.set_defaults(func=cmd_check)

    stats = sub.add_parser("stats", help="打印卡片与解析统计")
    stats.add_argument("--root", help="项目根（默认：最近的 .git）")
    stats.set_defaults(func=cmd_stats)

    db_parser = sub.add_parser("db", help="SQLite 索引")
    db_sub = db_parser.add_subparsers(dest="db_command", required=True)
    db_build = db_sub.add_parser("build", help="重建索引")
    db_build.add_argument("--root", help="项目根（默认：最近的 .git）")
    db_build.set_defaults(func=cmd_db)
    db_sync = db_sub.add_parser("sync", help="仅在文件变化时重建索引")
    db_sync.add_argument("--root", help="项目根（默认：最近的 .git）")
    db_sync.set_defaults(func=cmd_db, sync=True)

    query = sub.add_parser("query", help="查询索引：callers/callees/impact/raises/effects/symbol")
    query.add_argument("what", choices=["callers", "callees", "impact",
                                        "raises", "effects", "symbol"])
    query.add_argument("name", help="符号名（raises/effects 时为异常/效果名）")
    query.add_argument("--root", help="项目根（默认：最近的 .git）")
    query.add_argument("--depth", type=int, default=2, help="impact 深度，默认 2")
    query.set_defaults(func=cmd_query)

    skill = sub.add_parser("skill", help="SKILL 安装")
    skill_sub = skill.add_subparsers(dest="skill_command", required=True)
    skill_install = skill_sub.add_parser("install", help="安装 SKILL")
    skill_install.add_argument("--dest", default="~/.dsh/skills",
                               help="skills 根目录，默认 ~/.dsh/skills")
    skill_install.set_defaults(func=cmd_skill_install)

    prompt = sub.add_parser("prompt", help="打印生成提示（与安装版本一致）")
    prompt.set_defaults(func=cmd_prompt)

    rules = sub.add_parser("rules", help="打印规则表")
    rules.add_argument("--json", action="store_true")
    rules.add_argument("--markdown", action="store_true")
    rules.set_defaults(func=cmd_rules)

    brief = sub.add_parser("brief", help="组装生成上下文（规则 + 现有符号）")
    brief.add_argument("--root", help="项目根（默认：最近的 .git）")
    brief.add_argument("--file", help="目标文件（相对项目根）")
    brief.add_argument("--symbol", action="append", default=[],
                       help="目标符号（可重复；限定名或名字）")
    brief.add_argument("--depth", type=int, default=0,
                       help="附带几跳邻居（调用方/被调方），默认 0")
    brief.add_argument("--no-cache", action="store_true",
                       help="不读写 .code-standard/cache.pkl")
    brief.add_argument("--no-prompt", action="store_true",
                       help="只输出项目上下文，不重复规则提示")
    brief.set_defaults(func=cmd_brief)

    init = sub.add_parser("init", help="初始化项目策略（配置 + AGENTS.md）")
    init.add_argument("--root", help="项目根（默认：当前目录）")
    init.add_argument("--strict", action="store_true", help="默认开启严格模式")
    init.add_argument("--skip-dirs", nargs="*", default=[],
                      help="额外跳过的目录名")
    init.add_argument("--force", action="store_true", help="覆盖已有配置")
    init.set_defaults(func=cmd_init)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。

    @effect: state
    @calls: build_parser
    @raises: -
    @pre:
    @mutates:
    """
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
