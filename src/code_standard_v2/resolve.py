"""调用解析：把调用表达式尽量落到项目内某个卡片，解析不出就标 name_only。

@stable
@calls: -
"""
from __future__ import annotations

import builtins
from dataclasses import dataclass, field

from .model import Card, ImportFact
from .tags import EffectError, parse_effect


@dataclass
class Project:
    """项目级索引：文件、模块、顶层名、类成员、import 别名。

    """

    cards: list[Card]
    imports: dict[str, list[ImportFact]]
    files: dict[str, dict[str, Card]]
    modules: dict[str, dict[str, Card]]
    module_names: set[str]
    module_prefixes: set[str]
    top_by_name: dict[str, list[Card]]
    alias: dict[str, dict[str, tuple]]
    module_files: dict[str, set[str]]
    wildcards: dict[str, list[str]] = field(default_factory=dict)
    mro_cache: dict[str, list[Card]] = field(default_factory=dict)

    def nearest_class(self, caller: Card) -> Card | None:
        """沿限定名向上找最近的类卡片。

        @pure
        @calls: -
        @raises: -
        @pre:
        @mutates:
        """
        parts = caller.qname.split(".")
        for end in range(len(parts), 0, -1):
            qname = ".".join(parts[:end])
            card = self.files.get(caller.file, {}).get(qname)
            if card is not None and card.kind == "class":
                return card
        return None

    def _member(self, card: Card, name: str) -> Card | None:
        """取类的直接成员。

        @pure
        @calls: -
        @raises: -
        @pre:
        @mutates:
        """
        return self.files.get(card.file, {}).get(f"{card.qname}.{name}")

    def _member_chain(self, card: Card, parts: list[str]) -> Card | None:
        """沿 `Cls.a.b` 逐级取成员（支持继承）。

        @pure
        @calls: Project._member_with_bases
        @raises: -
        @pre:
        @mutates:
        """
        current = card
        for part in parts:
            if current.kind != "class":
                return None
            nxt = self._member_with_bases(current, part)
            if nxt is None:
                return None
            current = nxt
        return current

    def _base_card(self, card: Card, expr: str) -> Card | None:
        """把基类表达式解析成类卡片。

        @pure
        @calls: Project._resolve_dotted
        @raises: -
        @pre:
        @mutates:
        """
        text = " ".join((expr or "").split())
        if not text:
            return None
        if "." not in text:
            found = self.files.get(card.file, {}).get(text)
            return found if found is not None and found.kind == "class" else None
        found = self._resolve_dotted(text.split("."))
        return found if found is not None and found.kind == "class" else None

    def _c3_merge(self, sequences: list[list[Card]]) -> list[Card]:
        """C3 线性化合并。

        @pure
        @calls: -
        @raises: -
        @pre:
        @mutates:
        """
        seqs = [list(seq) for seq in sequences if seq]
        out: list[Card] = []
        while seqs:
            head: Card | None = None
            for seq in seqs:
                candidate = seq[0]
                if all(candidate not in other[1:] for other in seqs):
                    head = candidate
                    break
            if head is None:
                for seq in seqs:
                    for item in seq:
                        if item not in out:
                            out.append(item)
                return out
            out.append(head)
            for seq in seqs:
                if seq and seq[0] is head:
                    seq.pop(0)
            seqs = [seq for seq in seqs if seq]
        return out

    def _mro(self, card: Card, stack: set[str] | None = None) -> list[Card]:
        """计算类的方法解析顺序（C3），带缓存与环保护。

        @pure
        @calls: Project._base_card, Project._c3_merge, Project._mro
        @raises: -
        @pre:
        @mutates:
        """
        key = f"{card.file}:{card.qname}"
        cached = self.mro_cache.get(key)
        if cached is not None:
            return cached
        if stack is None:
            stack = set()
        if key in stack:
            return [card]
        stack.add(key)
        bases: list[Card] = []
        for expr in card.bases:
            base = self._base_card(card, expr)
            if base is not None and base.kind == "class":
                bases.append(base)
        merged = self._c3_merge(
            [self._mro(base, stack) for base in bases] + [list(bases)])
        result = [card] + [item for item in merged if item is not card]
        seen: set[str] = set()
        unique: list[Card] = []
        for item in result:
            item_key = f"{item.file}:{item.qname}"
            if item_key in seen:
                continue
            seen.add(item_key)
            unique.append(item)
        self.mro_cache[key] = unique
        stack.discard(key)
        return unique

    def _member_with_bases(self, card: Card, name: str) -> Card | None:
        """按 C3 MRO 顺序找成员（本类优先，再基类）。

        @pure
        @calls: Project._mro, Project._member
        @raises: -
        @pre:
        @mutates:
        """
        for current in self._mro(card):
            member = self._member(current, name)
            if member is not None:
                return member
        return None

    def _resolve_type(self, annotation: str, caller: Card) -> Card | None:
        """把简单注解（含 Optional/list 包装）解析成类卡片。

        @pure
        @calls: Project.resolve
        @raises: -
        @pre:
        @mutates:
        """
        text = annotation.strip()
        if not text:
            return None
        for prefix in ("Optional[", "list[", "List[", "Sequence[", "Iterable[",
                       "set[", "Set[", "tuple[", "Tuple["):
            if text.startswith(prefix) and text.endswith("]"):
                text = text[len(prefix):-1].strip()
                break
        if "|" in text:
            text = text.split("|")[0].strip()
        if text in ("", "None", "Any", "object"):
            return None
        target, _confidence = self.resolve(text, caller)
        return target if target is not None and target.kind == "class" else None

    def _protocol_targets(self, site: dict, caller: Card) -> list[Card]:
        """把协议调用点解析成目标方法卡。

        @pure
        @calls: Project.nearest_class, Project._resolve_type, Project._member_with_bases, Project.resolve
        @raises: -
        @pre:
        @mutates:
        """
        kind = site["kind"]
        expr = site["expr"]
        annotation = site["annotation"]
        if kind == "property":
            owner = None
            if expr.startswith(("self.", "cls.")):
                owner = self.nearest_class(caller)
            elif annotation:
                owner = self._resolve_type(annotation, caller)
            if owner is None:
                return []
            target = self._member_with_bases(owner, f"{site['attr']}.setter")
            return [target] if target is not None else []

        owner = self._resolve_type(annotation, caller) if annotation else None
        if owner is None:
            text = expr[:-2] if expr.endswith("()") else expr
            if text in ("self", "cls"):
                owner = self.nearest_class(caller)
            else:
                found, _confidence = self.resolve(text, caller)
                owner = found if found is not None and found.kind == "class" else None
        if owner is None:
            return []
        names: list[str] = []
        if kind == "with":
            names = ["__enter__", "__exit__"]
        elif kind == "for":
            names = ["__iter__"]
        elif kind == "call":
            names = ["__call__"]
        elif kind == "binop" and site["dunder"]:
            names = [site["dunder"]]
        out: list[Card] = []
        for name in names:
            target = self._member_with_bases(owner, name)
            if target is not None:
                out.append(target)
        return out

    def _from_module(self, module: str, parts: list[str]) -> Card | None:
        """在模块命名空间里走 `name` 或 `Class.method`。

        @pure
        @calls: Project._member_chain
        @raises: -
        @pre:
        @mutates:
        """
        if not parts:
            return None
        namespace = self.modules.get(module)
        if namespace is None:
            return None
        card = namespace.get(parts[0])
        if card is not None:
            rest = parts[1:]
            if not rest:
                return card
            return self._member_chain(card, rest)
        return None

    def _lookup_reexport(self, module: str, name: str,
                         seen: set[tuple[str, str]] | None = None) -> Card | None:
        """顺着模块的 `__init__.py` 导入再找一层（重导出）。

        @pure
        @calls: Project._from_module, Project._lookup_reexport
        @raises: -
        @pre:
        @mutates:
        """
        if seen is None:
            seen = set()
        key = (module, name)
        if key in seen:
            return None
        seen.add(key)
        for file in self.module_files.get(module, ()):
            for item in self.imports.get(file, ()):
                if item.kind == "from" and item.name:
                    exposed = item.alias or item.name
                    if exposed != name:
                        continue
                    found = self._from_module(item.module, [item.name])
                    if found is None:
                        found = self._lookup_reexport(item.module, item.name, seen)
                    if found is not None:
                        return found
                elif item.kind == "wildcard":
                    found = self._from_module(item.module, [name])
                    if found is None:
                        found = self._lookup_reexport(item.module, name, seen)
                    if found is not None:
                        return found
        return None

    def _resolve_dotted(self, parts: list[str]) -> Card | None:
        """对 `a.b.c` 尝试所有模块前缀，再解析剩余部分（含重导出）。

        @pure
        @calls: Project._from_module, Project._lookup_reexport, Project._member_chain
        @raises: -
        @pre:
        @mutates:
        """
        for end in range(len(parts) - 1, 0, -1):
            module = ".".join(parts[:end])
            if module not in self.module_names and module not in self.module_files:
                continue
            found = self._from_module(module, parts[end:])
            if found is not None:
                return found
            head = parts[end]
            rest = parts[end + 1:]
            found = self._lookup_reexport(module, head, set())
            if found is not None:
                return self._member_chain(found, rest) if rest else found
        return None

    def _alias_symbol(self, alias: tuple) -> Card | None:
        """把 from-import 的别名落到符号；失败时顺着重导出再找。

        @pure
        @calls: Project._from_module, Project._lookup_reexport
        @raises: -
        @pre:
        @mutates:
        """
        if alias[0] != "symbol":
            return None
        found = self._from_module(alias[1], [alias[2]])
        if found is not None:
            return found
        return self._lookup_reexport(alias[1], alias[2], set())

    def resolve(self, expr: str, caller: Card) -> tuple[Card | None, str]:
        """解析一次调用，返回 (目标卡片 | None, 置信度)。

        @pure
        @calls: Project.nearest_class, Project._base_card, Project._member_with_bases, Project._member_chain, Project._resolve_dotted, Project._alias_symbol, Project._from_module
        @raises: -
        @pre:
        @mutates:
        """
        text = " ".join((expr or "").split())
        if not text or text in ("self", "cls"):
            return None, "name_only"

        if text.startswith("super("):
            owner = self.nearest_class(caller)
            if owner is None or "." not in text:
                return (None, "builtin") if owner is not None else (None, "name_only")
            attr = text.rsplit(".", 1)[-1]
            for base_expr in owner.bases:
                base = self._base_card(owner, base_expr)
                if base is None:
                    continue
                found = self._member_with_bases(base, attr)
                if found is not None:
                    return found, "self"
            return None, "name_only"

        if text.startswith("self.") or text.startswith("cls."):
            owner = self.nearest_class(caller)
            if owner is None:
                return None, "name_only"
            parts = text.split(".")[1:]
            first = self._member_with_bases(owner, parts[0])
            if first is None:
                return None, "name_only"
            if len(parts) == 1:
                return first, "exact"
            chain = self._member_chain(first, parts[1:])
            return (chain, "exact") if chain else (None, "name_only")

        if "." in text:
            local = self.files.get(caller.file, {}).get(text)
            if local is not None:
                return local, "exact"
            parts = text.split(".")
            top = self.files.get(caller.file, {}).get(parts[0])
            if top is not None and top.kind == "class":
                chain = self._member_chain(top, parts[1:])
                return (chain, "exact") if chain else (None, "name_only")
            alias = self.alias.get(caller.file, {}).get(parts[0])
            if alias is not None:
                if alias[0] == "module":
                    found = self._resolve_dotted([alias[1]] + parts[1:])
                    return (found, "exact") if found else (None, "name_only")
                symbol = self._alias_symbol(alias)
                if symbol is not None:
                    chain = (symbol if len(parts) == 1
                             else self._member_chain(symbol, parts[1:]))
                    return (chain, "exact") if chain else (None, "name_only")
                if alias[0] == "symbol":
                    submodule = (f"{alias[1]}.{alias[2]}" if alias[1]
                                 else alias[2])
                    if submodule in self.module_names:
                        found = self._resolve_dotted([submodule] + parts[1:])
                        return (found, "exact") if found else (None, "name_only")
            found = self._resolve_dotted(parts)
            return (found, "exact") if found else (None, "name_only")

        scope_parts = caller.qname.split(".")
        for end in range(len(scope_parts), 0, -1):
            candidate = ".".join(scope_parts[:end] + [text])
            card = self.files.get(caller.file, {}).get(candidate)
            if card is not None:
                return card, "exact"
        top = self.files.get(caller.file, {}).get(text)
        if top is not None:
            return top, "exact"
        alias = self.alias.get(caller.file, {}).get(text)
        if alias is not None and alias[0] == "symbol":
            symbol = self._alias_symbol(alias)
            if symbol is not None:
                return symbol, "exact"
        for module in self.wildcards.get(caller.file, []):
            symbol = self._from_module(module, [text])
            if symbol is not None:
                return symbol, "exact"
        if text in dir(builtins):
            return None, "builtin"
        candidates = [card for card in self.top_by_name.get(text, [])
                      if card.kind in ("function", "class")]
        if len(candidates) == 1:
            return candidates[0], "inferred"
        if len(candidates) > 1:
            return None, "ambiguous"
        return None, "name_only"


def build_project(cards: list[Card], imports: dict[str, list[ImportFact]]) -> Project:
    """建立项目索引（模块、顶层名、类成员、import 别名）。

    @pure
    @calls: Project
    @raises: -
    @pre:
    @mutates:
    """
    files: dict[str, dict[str, Card]] = {}
    modules: dict[str, dict[str, Card]] = {}
    top_by_name: dict[str, list[Card]] = {}
    module_names: set[str] = set()

    for card in cards:
        if card.kind == "module":
            module_names.add(card.module)
            continue
        files.setdefault(card.file, {})[card.qname] = card
        if "." not in card.qname and card.kind in ("function", "class"):
            modules.setdefault(card.module, {})[card.name] = card
            top_by_name.setdefault(card.name, []).append(card)

    prefixes: set[str] = set()
    for module in module_names:
        parts = module.split(".")
        for end in range(1, len(parts) + 1):
            prefixes.add(".".join(parts[:end]))

    alias: dict[str, dict[str, tuple]] = {}
    wildcards: dict[str, list[str]] = {}
    module_files: dict[str, set[str]] = {}
    for card in cards:
        module_files.setdefault(card.module, set()).add(card.file)
    for file, items in imports.items():
        for item in items:
            if item.kind == "module":
                if item.alias:
                    alias.setdefault(file, {})[item.alias] = ("module", item.module)
            elif item.kind == "wildcard":
                wildcards.setdefault(file, []).append(item.module)
            elif item.kind == "from" and item.name and item.alias:
                alias.setdefault(file, {})[item.alias] = (
                    "symbol", item.module, item.name)

    return Project(cards, imports, files, modules, module_names, prefixes,
                   top_by_name, alias, module_files, wildcards)


def resolve_project(project: Project) -> dict:
    """为每张卡填入 resolved_calls，并反向填入 called_by。

    @effect: state
    @calls: resolve_project.collect, tags.parse_effect, resolve_project.collect_effects
    @raises: -
    @pre:
    @mutates:
    """
    stats = {"calls_total": 0, "resolved": 0, "by_confidence": {}}
    for card in project.cards:
        if card.kind == "module":
            continue
        for call in card.calls:
            target, confidence = project.resolve(call.expr, card)
            stats["calls_total"] += 1
            stats["by_confidence"][confidence] = (
                stats["by_confidence"].get(confidence, 0) + 1)
            entry = {
                "name": call.name,
                "expr": call.expr,
                "line": call.line,
                "confidence": confidence,
                "in_lambda": call.in_lambda,
                "target": None,
            }
            if target is not None:
                stats["resolved"] += 1
                entry["target"] = {
                    "qname": target.qname,
                    "file": target.file,
                    "line": target.start_line,
                    "module": target.module,
                }
                target.called_by.append({
                    "qname": card.qname,
                    "file": card.file,
                    "line": call.line,
                    "confidence": confidence,
                })
            card.resolved_calls.append(entry)

    for card in project.cards:
        if card.kind == "module":
            continue
        for ref in card.references:
            target, confidence = project.resolve(ref["expr"], card)
            if target is None or target.kind not in ("function", "method"):
                continue
            card.resolved_references.append({
                "name": ref["name"],
                "expr": ref["expr"],
                "line": ref["line"],
                "context": ref["context"],
                "confidence": confidence,
                "target": {
                    "qname": target.qname,
                    "file": target.file,
                    "line": target.start_line,
                    "module": target.module,
                },
            })
            target.referenced_by.append({
                "qname": card.qname,
                "file": card.file,
                "line": ref["line"],
                "context": ref["context"],
                "confidence": confidence,
            })

    for card in project.cards:
        if card.kind == "module":
            continue
        for site in card.protocol_sites:
            for target in project._protocol_targets(site, card):
                card.protocol_calls.append({
                    "kind": site["kind"],
                    "expr": site["expr"],
                    "line": site["line"],
                    "confidence": "protocol",
                    "target": {
                        "qname": target.qname,
                        "file": target.file,
                        "line": target.start_line,
                        "module": target.module,
                    },
                })
                target.protocol_called_by.append({
                    "qname": card.qname,
                    "file": card.file,
                    "line": site["line"],
                    "kind": site["kind"],
                })

    for card in project.cards:
        card.called_by.sort(key=lambda item: (item["file"], item["line"]))
        card.referenced_by.sort(key=lambda item: (item["file"], item["line"]))

    functions = {(card.file, card.qname): card
                 for card in project.cards
                 if card.kind in ("function", "method")}
    direct = {key: {item.type for item in card.raises if item.type}
              for key, card in functions.items()}
    edges: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for key, card in functions.items():
        for call in card.resolved_calls:
            target = call.get("target")
            if target is None:
                continue
            target_key = (target["file"], target["qname"])
            if target_key in functions:
                edges.setdefault(key, []).append(target_key)
    memo: dict[tuple[str, str], set[str]] = {}

    def collect(key: tuple[str, str], stack: set[tuple[str, str]]) -> set[str]:
        """递归收集沿调用链可能逃逸的异常（带环保护与记忆化）。

        @pure
        @raises: -
        @pre:
        @mutates: stack
        @calls: resolve_project.collect
        """
        if key in memo:
            return memo[key]
        if key in stack:
            return set()
        stack.add(key)
        found = set(direct.get(key, set()))
        for nxt in edges.get(key, []):
            found |= collect(nxt, stack)
        stack.discard(key)
        memo[key] = found
        return found

    for key, card in functions.items():
        card.propagated_raises = sorted(collect(key, set()))

    direct_effects: dict[tuple[str, str], set[str]] = {}
    for key, card in functions.items():
        kinds: set[str] = set()
        for value in card.tag_values("effect"):
            try:
                for kind, _target in parse_effect(value):
                    if kind != "none":
                        kinds.add(kind)
            except EffectError:
                continue
        direct_effects[key] = kinds
    effect_memo: dict[tuple[str, str], set[str]] = {}

    def collect_effects(key: tuple[str, str],
                        stack: set[tuple[str, str]]) -> set[str]:
        """递归收集沿调用链可能发生的效果（带环保护与记忆化）。

        @pure
        @raises: -
        @pre:
        @mutates: stack
        @calls: resolve_project.collect_effects
        """
        if key in effect_memo:
            return effect_memo[key]
        if key in stack:
            return set()
        stack.add(key)
        found = set(direct_effects.get(key, set()))
        for nxt in edges.get(key, []):
            found |= collect_effects(nxt, stack)
        stack.discard(key)
        effect_memo[key] = found
        return found

    for key, card in functions.items():
        card.propagated_effects = sorted(collect_effects(key, set()))
    return stats
