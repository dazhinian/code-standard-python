"""标签注册表与 docstring 解析。

效果（effect）是结构化枚举，不再是自由文本：
`@effect: db.write(claims), log`；`@pure` 等价于“没有效果”。

@stable
@calls: -
"""
from __future__ import annotations

import re

TAG_RE = re.compile(r"^@([A-Za-z][\w-]*)(?::\s*(.*))?$")

# 效果种类（目标可选，建议写具体目标：db.write(claims)）
EFFECT_KINDS = {
    "none", "db.read", "db.write", "file.read", "file.write",
    "net", "log", "env", "clock", "random", "process", "state", "ui",
}

# 函数/方法标签： 标签名 -> 是否必须有值
FUNC_TAGS: dict[str, bool] = {
    "pure": False,
    "effect": True,
    "calls": False,
    "raises": True,
    "pre": True,
    "thread-safe": False,
    "not-thread-safe": False,
    "deprecated": True,
    "mutates": True,
    "env": True,
    "invariant": True,
    "idempotent": False,
    "transactional": False,
    "pii": True,
    "param": True,
    "returns": True,
}

MODULE_TAGS: dict[str, bool] = {
    "stable": False,
    "experimental": False,
    "calls": False,
    "depends": True,
}

CLASS_TAGS: dict[str, bool] = {
    "invariant": True,
}

# v1 标签：识别但不再生效（@calls 已升级为工具生成的一等标签）
LEGACY_TAGS = {"called-by", "depends", "entry", "side-effect"}


class EffectError(ValueError):
    """效果声明格式或种类错误。"""


def parse_tags(docstring: str, start_line: int) -> tuple[dict[str, list[str]],
                                                        dict[str, int]]:
    """解析 docstring 中的标签，返回 (值表, 首次出现行号)。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    tags: dict[str, list[str]] = {}
    lines: dict[str, int] = {}
    for offset, raw in enumerate(docstring.splitlines()):
        text = raw.strip()
        match = TAG_RE.match(text)
        if not match:
            continue
        name = match.group(1)
        value = (match.group(2) or "").strip()
        tags.setdefault(name, []).append(value)
        lines.setdefault(name, start_line + offset)
    return tags, lines


def summary_of(docstring: str) -> str:
    """取 docstring 首行作为摘要；首行是标签则视为没有摘要。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    for raw in docstring.splitlines():
        text = raw.strip()
        if text:
            return "" if text.startswith("@") else text
    return ""


def first_token(value: str) -> str:
    """取标签值里的第一个记号（用于 `@raises: ValueError 说明`）。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    text = value.strip().replace(",", " ").split()
    return text[0].rstrip(".:") if text else ""


def _split_entries(value: str) -> list[str]:
    """按顶层逗号切分效果条目，括号内的逗号不切。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    entries: list[str] = []
    depth = 0
    current = ""
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            entries.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        entries.append(current.strip())
    return [item for item in entries if item]


def parse_effect(value: str) -> list[tuple[str, str | None]]:
    """解析 `db.write(claims), log` 形式的效果声明。

    @pure
    @raises: EffectError 当种类未知或括号不配对
    @calls: _split_entries, EffectError
    @pre:
    @mutates:
    """
    entries = _split_entries(value)
    if not entries:
        raise EffectError("效果声明为空")
    parsed: list[tuple[str, str | None]] = []
    for entry in entries:
        if entry.startswith("none"):
            if not entry == "none":
                raise EffectError(f"none 不能带目标：{entry}")
            if len(entries) > 1:
                raise EffectError("none 不能与其它效果并列")
            parsed.append(("none", None))
            continue
        if "(" in entry:
            if not entry.endswith(")"):
                raise EffectError(f"括号不配对：{entry}")
            kind, target = entry[:-1].split("(", 1)
            kind, target = kind.strip(), target.strip()
        else:
            kind, target = entry, None
        if kind not in EFFECT_KINDS:
            raise EffectError(f"未知效果种类：{kind}")
        parsed.append((kind, target))
    return parsed


def generated_calls_value(calls: list[str]) -> str:
    """把生成的调用名列表渲染成 `@calls:` 的值。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return ", ".join(calls) if calls else "-"


def normalize_calls_value(value: str) -> set[str]:
    """把 `@calls:` 的值归一成集合，用于与工具生成结果比对。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    if value.strip() in ("", "-"):
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}
