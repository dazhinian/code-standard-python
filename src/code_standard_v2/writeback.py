"""把生成好的 `@calls:` 与固定空槽写回 docstring（不动其它内容）。

固定槽位：`@calls`（工具生成）、`@raises`、`@pre`、`@mutates`。
没有内容时写 `-`（明确无）或留空（未声明）；工具只保证槽位存在。

@stable
@calls: -
"""
from __future__ import annotations

import ast
import os
import re

from .inline import inline_calls, module_calls
from .model import Card
from .tags import generated_calls_value


def _function_lines(tree: ast.Module) -> dict[int, ast.AST]:
    """建立 def 行号 -> 函数节点映射（行号天然唯一，属性/重载不冲突）。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    found: dict[int, ast.AST] = {}
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.lineno] = node
            stack.extend(node.body)
            continue
        stack.extend(ast.iter_child_nodes(node))
    return found


def _docstring_line_range(node: ast.AST) -> tuple[int, int, bool] | None:
    """返回 docstring 的 1-based 行区间与是否单行。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    body = getattr(node, "body", [])
    if not body or not isinstance(body[0], ast.Expr):
        return None
    value = body[0].value
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        return None
    start = body[0].lineno
    end = body[0].end_lineno or start
    return start, end, start == end


def _find_tag(lines: list[str], start: int, end: int, name: str) -> int | None:
    """在 docstring 行区间内找某个标签所在行（0-based）。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    prefix = f"@{name}:"
    for index in range(start - 1, end):
        if lines[index].strip().startswith(prefix):
            return index
    return None


def _raise_types(card: Card) -> list[str]:
    """取函数体直接 raise 的异常类型（去重排序）。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    types: set[str] = set()
    for item in card.raises:
        if item.type:
            types.add(item.type)
    return sorted(types)


def update_source(path: str, cards: list[Card]) -> tuple[str, int]:
    """返回 (新源码, 被更新的函数数)；不写盘。

    @effect: file.read
    @calls: _function_lines, _docstring_line_range, tags.generated_calls_value, inline.module_calls, _find_tag, inline.inline_calls, _raise_types
    @raises: -
    @pre:
    @mutates:
    """
    with open(path, encoding="utf-8-sig") as handle:
        source = handle.read()
    tree = ast.parse(source, filename=path)
    nodes = _function_lines(tree)
    lines = source.splitlines(keepends=True)
    edits: list[tuple[int, str, object]] = []  # (index, kind, text|block)
    changed = 0
    project_names = {card.name for card in cards if card.kind != "module"}

    for card in cards:
        if card.kind != "module":
            continue
        span = _docstring_line_range(tree)
        if span is None:
            continue
        start, end, single = span
        desired = generated_calls_value(module_calls(card, project_names))
        if single:
            match = re.match(r'^(\s*)("""|\'\'\')(.*?)(\2)\s*$', lines[start - 1])
            if match is None:
                continue
            indent, quote, content = match.group(1), match.group(2), match.group(3)
            block = [f"{indent}{quote}{content}\n",
                     f"{indent}@calls: {desired}\n",
                     f"{indent}{quote}\n"]
            edits.append((start - 1, "replace", "".join(block)))
            changed += 1
            continue
        closing = end - 1
        if not (lines[closing].strip().startswith('"""')
                or lines[closing].strip().startswith("'''")):
            continue
        indent = lines[closing][:len(lines[closing]) - len(lines[closing].lstrip())]
        calls_index = _find_tag(lines, start, end, "calls")
        calls_line = f"{indent}@calls: {desired}\n"
        if calls_index is not None:
            if lines[calls_index].rstrip("\n") != calls_line.rstrip("\n"):
                edits.append((calls_index, "replace", calls_line))
                changed += 1
        else:
            edits.append((closing, "insert", [calls_line]))
            changed += 1

    for card in cards:
        if card.kind not in ("function", "method") or card.is_overload:
            continue
        node = nodes.get(card.def_line)
        if node is None:
            continue
        span = _docstring_line_range(node)
        if span is None:
            continue
        start, end, single = span
        desired = generated_calls_value(inline_calls(card))
        if single:
            match = re.match(r'^(\s*)("""|\'\'\')(.*?)(\2)\s*$', lines[start - 1])
            if match is None:
                continue
            indent, quote, content = match.group(1), match.group(2), match.group(3)
            block = [f"{indent}{quote}{content}\n",
                     f"{indent}@calls: {desired}\n"]
            for exc in _raise_types(card) or ["-"]:
                block.append(f"{indent}@raises: {exc}\n")
            block.append(f"{indent}@pre:\n")
            block.append(f"{indent}@mutates:\n")
            block.append(f"{indent}{quote}\n")
            edits.append((start - 1, "replace", "".join(block)))
            changed += 1
            continue
        closing = end - 1
        if not (lines[closing].strip().startswith('"""')
                or lines[closing].strip().startswith("'''")):
            continue  # 收尾引号与正文同行，交给人工处理
        indent = lines[closing][:len(lines[closing]) - len(lines[closing].lstrip())]
        block: list[str] = []

        calls_index = _find_tag(lines, start, end, "calls")
        desired = generated_calls_value(inline_calls(card))
        calls_line = f"{indent}@calls: {desired}\n"
        if calls_index is not None:
            if lines[calls_index].rstrip("\n") != calls_line.rstrip("\n"):
                edits.append((calls_index, "replace", calls_line))
                changed += 1
        else:
            block.append(calls_line)

        if _find_tag(lines, start, end, "raises") is None:
            for exc in _raise_types(card) or ["-"]:
                block.append(f"{indent}@raises: {exc}\n")
        if _find_tag(lines, start, end, "pre") is None:
            block.append(f"{indent}@pre:\n")
        if _find_tag(lines, start, end, "mutates") is None:
            block.append(f"{indent}@mutates:\n")

        if block:
            edits.append((closing, "insert", block))
            changed += 1

    for index, kind, payload in sorted(edits, key=lambda item: item[0], reverse=True):
        if kind == "replace":
            lines[index] = payload  # type: ignore[assignment]
        else:
            lines[index:index] = payload  # type: ignore[assignment]
    return "".join(lines), changed


def write_source(path: str, text: str) -> None:
    """原子写回源码。

    @effect: file.write
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    temp = path + ".cs2.tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temp, path)
