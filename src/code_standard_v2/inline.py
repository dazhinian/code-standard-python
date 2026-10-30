"""内联调用声明：从卡片事实生成 `@calls:` 的值，并做归一化比对。

只写项目内、精确解析到的调用（跨文件加模块前缀）；外部包、builtin、
未解析目标不写入——它们在代码里本来就看得见，写进去只会制造噪声。

@stable
@calls: -
"""
from __future__ import annotations

from .model import Card

INTERNAL = ("exact", "inferred")


def _module_label(caller_module: str, target_module: str) -> str:
    """取目标模块相对调用方的短标签（同包只留末段）。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    caller = caller_module.split(".") if caller_module else []
    target = target_module.split(".") if target_module else []
    common = 0
    for left, right in zip(caller, target):
        if left != right:
            break
        common += 1
    rest = target[common:]
    return ".".join(rest) if rest else target[-1] if target else ""


def inline_calls(card: Card) -> list[str]:
    """按调用出现顺序生成内联调用名（去重，跨文件加模块短前缀）。

    lambda 内的调用加 `~` 前缀：`~helper` 表示“执行 lambda 时才调用”。

    @pure
    @calls: _module_label
    @raises: -
    @pre:
    @mutates:
    """
    out: list[str] = []
    seen: set[str] = set()
    for call in card.resolved_calls:
        if call.get("confidence") not in INTERNAL:
            continue
        target = call.get("target")
        if not target:
            continue
        qname = target["qname"]
        module = target.get("module") or ""
        if target.get("file") != card.file and module:
            qname = f"{_module_label(card.module, module)}.{qname}"
        token = f"~{qname}" if call.get("in_lambda") else qname
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def module_calls(card: Card, project_names: set[str]) -> list[str]:
    """模块级（import 时执行）调用项目函数的名字，去重保序。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    out: list[str] = []
    seen: set[str] = set()
    for call in card.definition_calls:
        if call.name not in project_names or call.name in seen:
            continue
        seen.add(call.name)
        out.append(call.name)
    return out
