"""对账规则：声明与事实比对，矛盾即失败。

  C0 源码错误                  C1 模块缺摘要              C2 模块稳定性
  C3 类缺摘要                  C4 函数缺摘要              C5 type hints 不全
  C6 纯度/效果声明缺失或冲突   C7 无效果声明与函数体冲突  C8 传递到有副作用函数
  C9 raise 未声明              C10 凭空声明异常           C11 可变默认参数
  C12 标签缺值                 C13 线程安全标签冲突       C14 未知/越界标签
  C15 遗留关系标签             C16 废弃函数仍被调用       C20 旧 @side-effect
  C21 @calls 与事实不一致      C22 缺少 @calls

@stable
@calls: -
"""
from __future__ import annotations

from dataclasses import dataclass

from .inline import inline_calls, module_calls
from .model import Card
from .rules import RULES
from .tags import (CLASS_TAGS, FUNC_TAGS, LEGACY_TAGS, MODULE_TAGS, EffectError,
                   first_token, normalize_calls_value, parse_effect)


@dataclass
class Finding:
    """一条对账结果。"""

    file: str
    line: int
    symbol: str
    rule: str
    severity: str  # error | warning
    message: str
    fix: str


def _error(file: str, line: int, symbol: str, rule: str, message: str,
           fix: str) -> Finding:
    """构造 error 级结果。

    @pure
    @calls: Finding
    @raises: -
    @pre:
    @mutates:
    """
    return Finding(file, line, symbol, rule, "error", message, fix)


def _warning(file: str, line: int, symbol: str, rule: str, message: str,
             fix: str) -> Finding:
    """构造 warning 级结果。

    @pure
    @calls: Finding
    @raises: -
    @pre:
    @mutates:
    """
    return Finding(file, line, symbol, rule, "warning", message, fix)


def effect_entries(card: Card) -> tuple[list[tuple[str, str | None]], str | None]:
    """解析卡片上的全部 @effect 值，返回 (条目, 错误信息)。

    @pure
    @calls: tags.parse_effect
    @raises: -
    @pre:
    @mutates:
    """
    entries: list[tuple[str, str | None]] = []
    for value in card.tag_values("effect"):
        try:
            entries.extend(parse_effect(value))
        except EffectError as exc:
            return entries, str(exc)
    return entries, None


def purity_of(card: Card) -> str | None:
    """由 @pure/@effect 推纯度：pure / impure / None（缺失或冲突）。

    @pure
    @calls: effect_entries
    @raises: -
    @pre:
    @mutates:
    """
    if "pure" in card.tags:
        return "pure"
    entries, error = effect_entries(card)
    if error is None and entries:
        return "pure" if all(kind == "none" for kind, _ in entries) else "impure"
    if "side-effect" in card.legacy_tags:
        return "impure"
    return None


def declared_raises(card: Card) -> set[str]:
    """取 @raises 声明的异常类型集合。

    @pure
    @calls: tags.first_token
    @raises: -
    @pre:
    @mutates:
    """
    types: set[str] = set()
    for value in card.tag_values("raises"):
        token = first_token(value)
        if token and token != "-":
            types.add(token)
    return types


def _missing_hints(card: Card) -> list[str]:
    """列出缺注解的参数与返回值。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    missing: list[str] = []
    for index, param in enumerate(card.params):
        if index == 0 and param.name in ("self", "cls"):
            continue
        if not param.type:
            missing.append(param.name)
    if card.returns is None:
        missing.append("返回值")
    return missing


def _check_module(card: Card, strict: bool,
                  project_names: set[str]) -> list[Finding]:
    """C1/C2/C15/C21/C22：模块摘要、稳定性、遗留标签、import 期调用。

    @pure
    @calls: _error, _warning, inline.module_calls, tags.normalize_calls_value
    @raises: -
    @pre:
    @mutates:
    """
    out: list[Finding] = []
    if not card.summary:
        out.append(_error(card.file, 1, "<module>", "C1",
                          "模块缺少 docstring 摘要", "在文件首行写一句话说明模块职责"))
    has_stable = "stable" in card.tags
    has_experimental = "experimental" in card.tags
    if has_stable and has_experimental:
        out.append(_error(card.file, card.tag_lines.get("stable", 1), "<module>",
                          "C2", "@stable 与 @experimental 不能同存", "二选一"))
    if strict and not has_stable and not has_experimental:
        out.append(_error(card.file, 1, "<module>", "C2",
                          "--strict 要求模块声明 @stable 或 @experimental",
                          "加 @stable 或 @experimental"))
    if "depends" in card.legacy_tags:
        out.append(_warning(card.file, card.tag_lines.get("depends", 1), "<module>",
                            "C15", "遗留 @depends 已不参与规范",
                            "删除；依赖关系由卡片生成"))
    expected = module_calls(card, project_names)
    values = card.tag_values("calls")
    if not values:
        out.append(_warning(card.file, card.tag_lines.get("calls", 1), "<module>",
                            "C22", "模块 docstring 缺少 @calls",
                            "运行 `cs2 sync --write` 写入 import 期调用"))
    else:
        declared: set[str] = set()
        for value in values:
            declared |= normalize_calls_value(value)
        if declared != set(expected):
            missing = sorted(set(expected) - declared)
            extra = sorted(declared - set(expected))
            detail = []
            if missing:
                detail.append(f"缺少 {', '.join(missing)}")
            if extra:
                detail.append(f"多出 {', '.join(extra)}")
            out.append(_error(card.file, card.tag_lines.get("calls", 1), "<module>",
                              "C21", f"模块 @calls 与 import 期调用不一致（{'；'.join(detail)}）",
                              "运行 `cs2 sync --write` 重新生成"))
    return out


def _check_tags(card: Card) -> list[Finding]:
    """C12/C13/C14：标签值、互斥、作用域与效果格式。

    @pure
    @calls: _error, tags.parse_effect
    @raises: -
    @pre:
    @mutates:
    """
    out: list[Finding] = []
    if card.kind == "module":
        allowed = MODULE_TAGS
    elif card.kind == "class":
        allowed = CLASS_TAGS
    else:
        allowed = FUNC_TAGS
    for tag in card.tags:
        if tag in LEGACY_TAGS:
            continue
        if tag not in allowed:
            out.append(_error(card.file, card.tag_lines.get(tag, card.start_line),
                              card.name, "C14",
                              f"标签 @{tag} 不适用于{card.kind}或不存在",
                              "检查拼写；标签清单见 standard.md"))
    for tag, needs_value in allowed.items():
        if not needs_value:
            continue
        for value in card.tag_values(tag):
            if not value.strip() and tag not in ("raises", "pre", "mutates"):
                out.append(_error(card.file, card.tag_lines.get(tag, card.start_line),
                                  card.name, "C12",
                                  f"@{tag} 必须有值", f"改为 @{tag}: <说明>"))
    if ("thread-safe" in card.tags) and ("not-thread-safe" in card.tags):
        out.append(_error(card.file, card.tag_lines.get("thread-safe", card.start_line),
                          card.name, "C13",
                          "@thread-safe 与 @not-thread-safe 不能同存", "二选一"))
    for value in card.tag_values("effect"):
        try:
            parse_effect(value)
        except EffectError as exc:
            out.append(_error(card.file, card.tag_lines.get("effect", card.start_line),
                              card.name, "C14", f"@effect 无法解析：{exc}",
                              "格式：@effect: db.write(表名), log"))
    return out


def _check_function(card: Card) -> list[Finding]:
    """C4-C12：摘要、类型、效果、raise、可变默认参数。

    @pure
    @calls: _error, _missing_hints, effect_entries, _warning, declared_raises
    @raises: -
    @pre:
    @mutates:
    """
    out: list[Finding] = []
    if not card.summary:
        out.append(_error(card.file, card.start_line, card.name, "C4",
                          "函数缺少 docstring 摘要", "docstring 首行写一句话功能说明"))
    missing = _missing_hints(card)
    if missing:
        out.append(_error(card.file, card.start_line, card.name, "C5",
                          f"缺少 type hints: {', '.join(missing)}",
                          "为每个参数与返回值补注解（self/cls 除外）"))
    if card.mutable_defaults:
        out.append(_error(card.file, card.start_line, card.name, "C11",
                          f"可变默认参数: {', '.join(card.mutable_defaults)}",
                          "改用 None 哨兵并在函数体内新建"))
    entries, error = effect_entries(card)
    legacy = "side-effect" in card.legacy_tags
    if error is None:
        has_pure = "pure" in card.tags
        has_effect = bool(entries)
        pure_like = has_pure or (has_effect and all(kind == "none" for kind, _ in entries))
        impure_like = any(kind != "none" for kind, _ in entries) or legacy
        if has_pure and has_effect:
            out.append(_error(card.file, card.start_line, card.name, "C6",
                              "@pure 与 @effect 不能同存", "二选一"))
        elif not pure_like and not impure_like:
            out.append(_error(card.file, card.start_line, card.name, "C6",
                              "缺少 @pure 或 @effect",
                              "无副作用写 @pure；有副作用写 @effect: db.write(表名) 等"))
    is_pure = "pure" in card.tags or (
        error is None and entries and all(kind == "none" for kind, _ in entries))
    detected = sorted({source["kind"] for source in card.effect_sources})
    if is_pure:
        bad = sorted(set(card.purity_violations) | set(detected))
        if bad:
            out.append(_error(card.file, card.start_line, card.name, "C7",
                              f"声明无副作用，但函数体含: {', '.join(bad)}",
                              "改标 @effect: <种类>，或移除这些调用"))
    elif entries and detected:
        declared_kinds = {kind for kind, _ in entries}
        missing_effects = [kind for kind in detected if kind not in declared_kinds]
        if missing_effects:
            out.append(_warning(
                card.file, card.start_line, card.name, "C27",
                f"检测到效果但 @effect 未声明: {', '.join(missing_effects)}",
                "补 @effect: <种类>(<目标>)；若来自封装函数，可保留但需人工确认"))
    if not is_pure and entries:
        declared_kinds = {kind for kind, _ in entries}
        extra_effects = [kind for kind in card.propagated_effects
                         if kind not in declared_kinds]
        if extra_effects:
            out.append(_warning(
                card.file, card.start_line, card.name, "C28",
                f"下游效果未在本函数 @effect 声明: {', '.join(extra_effects)}",
                "把下游效果加进 @effect，或确认调用是否应被封装"))
    if card.env_names:
        declared_env: set[str] = set()
        for value in card.tag_values("env"):
            for item in value.split(","):
                if item.strip():
                    declared_env.add(item.strip())
        missing_env = [name for name in card.env_names if name not in declared_env]
        if missing_env:
            out.append(_warning(
                card.file, card.start_line, card.name, "C29",
                f"使用了环境变量但 @env 未声明: {', '.join(missing_env)}",
                "补 @env: NAME；或改为显式参数注入"))
    actual = {item.type for item in card.raises if item.type}
    declared = declared_raises(card)
    missing_raises = actual - declared
    if missing_raises:
        out.append(_error(card.file, card.start_line, card.name, "C9",
                          f"有 raise({', '.join(sorted(missing_raises))}) 但 @raises 未声明",
                          "为每个异常类型加一行 @raises: <类型> <说明>"))
    extra = declared - actual
    if extra and not card.calls:
        out.append(_warning(card.file, card.start_line, card.name, "C10",
                            f"@raises 声明了未直接 raise 的类型: {', '.join(sorted(extra))}",
                            "若来自下游传播，保留并在说明里写清来源；否则删除"))
    writes = [item["name"] for item in card.globals if item["kind"] == "write"]
    kinds = {kind for kind, _ in entries}
    if writes and "state" not in kinds:
        names = ", ".join(sorted(set(writes)))
        out.append(_warning(card.file, card.start_line, card.name, "C24",
                            f"写了 global/nonlocal 变量: {names}，但 @effect 未声明 state",
                            "改标 @effect: state(变量名)，说明改了哪个外部状态"))
    if card.mutated_params:
        declared_mutates: set[str] = set()
        for value in card.tag_values("mutates"):
            for item in value.split(","):
                token = item.strip()
                if token:
                    declared_mutates.add(token.split(".")[0].strip())
        missing_mutates = [name for name in card.mutated_params
                           if name not in declared_mutates]
        if missing_mutates:
            out.append(_warning(card.file, card.start_line, card.name, "C26",
                                f"参数被改写但 @mutates 未声明: {', '.join(missing_mutates)}",
                                "在 @mutates 里列出参数名；确实不改则删掉改写"))
    missing_slots = [name for name in ("raises", "pre", "mutates")
                     if name not in card.tags]
    if missing_slots:
        names = ", ".join("@" + name for name in missing_slots)
        out.append(_warning(card.file, card.start_line, card.name, "C23",
                            f"缺少固定槽位: {names}",
                            "运行 `cs2 sync --write` 补齐；无内容留空或用 -"))
    return out


def _check_calls(card: Card) -> list[Finding]:
    """C21/C22：内联 @calls 必须与工具生成结果一致。

    @pure
    @calls: inline.inline_calls, _warning, tags.normalize_calls_value, _error
    @raises: -
    @pre:
    @mutates:
    """
    expected = inline_calls(card)
    values = card.tag_values("calls")
    if not values:
        return [_warning(card.file, card.start_line, card.name, "C22",
                         "缺少 @calls（应由工具生成）",
                         "运行 `cs2 sync --write` 自动写入；不要手写")]
    declared: set[str] = set()
    for value in values:
        declared |= normalize_calls_value(value)
    if declared != set(expected):
        missing = sorted(set(expected) - declared)
        extra = sorted(declared - set(expected))
        detail = []
        if missing:
            detail.append(f"缺少 {', '.join(missing)}")
        if extra:
            detail.append(f"多出 {', '.join(extra)}")
        return [_error(card.file, card.start_line, card.name, "C21",
                       f"@calls 与事实不一致（{'；'.join(detail)}）",
                       "运行 `cs2 sync --write` 重新生成；不要手改这一行")]
    return []


def _purity_path(start: Card, edges: dict[tuple[str, str], list[tuple[str, str]]],
                 index: dict[tuple[str, str], Card],
                 max_depth: int = 3) -> list[Card] | None:
    """沿已解析调用边找一条通向有副作用函数的最短路径。

    @pure
    @calls: purity_of
    @raises: -
    @pre:
    @mutates:
    """
    key = (start.file, start.qname)
    queue: list[tuple[tuple[str, str], list[Card]]] = [(key, [])]
    seen = {key}
    while queue:
        node, path = queue.pop(0)
        if len(path) >= max_depth:
            continue
        for target in edges.get(node, []):
            if target in seen:
                continue
            seen.add(target)
            hit = index.get(target)
            if hit is None:
                continue
            trail = path + [hit]
            if purity_of(hit) == "impure":
                return trail
            queue.append((target, trail))
    return None


def _check_pure_calls(card: Card,
                      edges: dict[tuple[str, str], list[tuple[str, str]]],
                      index: dict[tuple[str, str], Card]) -> list[Finding]:
    """C8：无副作用声明经调用链到达有副作用函数（最多 3 跳）。

    @pure
    @calls: purity_of, _purity_path, _error
    @raises: -
    @pre:
    @mutates:
    """
    if purity_of(card) != "pure":
        return []
    path = _purity_path(card, edges, index)
    if path is None:
        return []
    trail = " → ".join(item.qname for item in path)
    return [_error(card.file, card.start_line, card.name, "C8",
                   f"声明无副作用，但经 {trail} 到达有副作用的函数",
                   "改标 @effect，或确认下游是否真有副作用")]


def _check_deprecated(card: Card, loose: dict[str, list[dict]]) -> list[Finding]:
    """C16：标了 @deprecated 但仍有调用者。

    @pure
    @calls: _error, _warning
    @raises: -
    @pre:
    @mutates:
    """
    if "deprecated" not in card.tags:
        return []
    exact = [item for item in card.called_by
             if item["confidence"] in ("exact", "inferred")]
    if exact:
        names = ", ".join(f"{item['qname']}@{item['file']}:{item['line']}"
                          for item in exact[:5])
        return [_error(card.file, card.start_line, card.name, "C16",
                       f"@deprecated 函数仍被调用: {names}",
                       "调用方改用 @deprecated 给出的替代方案")]
    others = [item for item in loose.get(card.name, [])
              if item["qname"] != card.qname]
    if others:
        names = ", ".join(f"{item['qname']}@{item['file']}:{item['line']}"
                          for item in others[:5])
        return [_warning(card.file, card.start_line, card.name, "C16",
                         f"@deprecated 函数有同名未解析调用者: {names}",
                         "用 grep 核实后再迁移或删除")]
    return []


def check_project(cards: list[Card], strict: bool = False) -> list[Finding]:
    """对整份卡片跑全部规则，并应用 strict 升级。

    @pure
    @calls: _check_module, _check_tags, _warning, _check_function, _check_calls, _check_pure_calls, _check_deprecated, Finding
    @raises: -
    @pre:
    @mutates:
    """
    index = {(card.file, card.qname): card for card in cards
             if card.kind != "module"}
    kinds = {(card.file, card.qname): card.kind for card in cards
             if card.kind != "module"}
    edges: dict[tuple[str, str], list[tuple[str, str]]] = {}
    loose: dict[str, list[dict]] = {}
    for card in cards:
        if card.kind == "module":
            continue
        key = (card.file, card.qname)
        for call in card.resolved_calls:
            target = call.get("target")
            if target:
                edges.setdefault(key, []).append((target["file"], target["qname"]))
            elif call.get("confidence") == "name_only":
                loose.setdefault(call["name"], []).append(
                    {"qname": card.qname, "file": card.file, "line": call["line"]})

    findings: list[Finding] = []
    files_with_legacy_relations: set[str] = set()
    files_with_old_effect: dict[str, int] = {}

    project_names = {item.name for item in cards if item.kind != "module"}

    for card in cards:
        if card.kind == "module":
            findings.extend(_check_module(card, strict, project_names))
            findings.extend(_check_tags(card))
            project_calls = [call for call in card.definition_calls
                             if call.name in project_names]
            if project_calls:
                names = ", ".join(sorted({call.name for call in project_calls}))
                findings.append(_warning(
                    card.file, 1, "<module>", "C25",
                    f"模块级代码在 import 时调用项目函数: {names}",
                    "确认是否有意；需要时移进函数或标注原因"))
            continue
        if card.kind == "class":
            if not card.summary:
                findings.append(_warning(card.file, card.start_line, card.name, "C3",
                                         "类缺少 docstring 摘要",
                                         "加一句话说明该类的职责与不变量"))
            findings.extend(_check_tags(card))
            continue
        if card.is_overload:
            findings.extend(_check_tags(card))
            continue
        findings.extend(_check_function(card))
        findings.extend(_check_tags(card))
        findings.extend(_check_calls(card))
        findings.extend(_check_pure_calls(card, edges, index))
        findings.extend(_check_deprecated(card, loose))
        if any(tag in card.legacy_tags for tag in ("called-by", "entry")):
            files_with_legacy_relations.add(card.file)
        if "side-effect" in card.legacy_tags:
            files_with_old_effect[card.file] = files_with_old_effect.get(card.file, 0) + 1

    for file in sorted(files_with_legacy_relations):
        findings.append(_warning(file, 1, "<file>", "C15",
                                 "本文件仍有遗留关系标签（@called-by/@entry）",
                                 "关系由卡片生成；删除标签"))
    for file, count in sorted(files_with_old_effect.items()):
        findings.append(_warning(file, 1, "<file>", "C20",
                                 f"本文件有 {count} 处旧 @side-effect",
                                 "改用结构化 @effect: db.write(表名), log 等"))

    if strict:
        findings = [
            Finding(item.file, item.line, item.symbol, item.rule, "error",
                    item.message, item.fix) if item.severity == "warning" else item
            for item in findings
        ]
    findings.sort(key=lambda item: (item.file, item.line, item.rule, item.symbol))
    return findings
