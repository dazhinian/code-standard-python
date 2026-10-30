"""AST 抽取：把源码变成模块/类/函数卡片（事实层，不含任何人工判断）。

@stable
@calls: -
"""
from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field

from .model import CallFact, Card, ImportFact, Param, RaiseFact
from .tags import LEGACY_TAGS, parse_tags, summary_of

_PURE_BUILTINS = {"print", "open", "input", "exec", "eval"}
_MUTABLE_CALLS = {"list", "dict", "set"}


def _split_legacy(tags: dict[str, list[str]],
                  tag_lines: dict[str, int]) -> tuple[
                      dict[str, list[str]], dict[str, list[str]], dict[str, int]]:
    """把 v1 的关系标签拆出来，v2 卡片只保留声明类标签。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    legacy = {name: values for name, values in tags.items()
              if name in LEGACY_TAGS}
    kept = {name: values for name, values in tags.items()
            if name not in LEGACY_TAGS}
    lines = {name: line for name, line in tag_lines.items()
             if name not in LEGACY_TAGS}
    return kept, legacy, lines


class SourceError(Exception):
    """源码无法读取或解析。

    """


@dataclass
class FileFacts:
    """单个文件的抽取结果。

    """

    cards: list[Card] = field(default_factory=list)
    imports: list[ImportFact] = field(default_factory=list)


def read_source(path: str) -> str:
    """按 UTF-8（容忍 BOM）读取源码。

    @effect: file.read
    @raises: SourceError 当文件不是可解码的 UTF-8
    @calls: SourceError
    @pre:
    @mutates:
    """
    try:
        with open(path, encoding="utf-8-sig") as handle:
            return handle.read()
    except OSError as exc:
        raise SourceError(f"无法读取文件: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise SourceError(f"非 UTF-8 源码: {exc}") from exc


def module_name(rel_path: str) -> str:
    """由仓库相对路径推出点分模块名（去掉 `src/`、`__init__`）。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    parts = rel_path[:-3].split("/") if rel_path.endswith(".py") else rel_path.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if len(parts) > 1 and parts[0] in ("src", "lib", "python"):
        parts = parts[1:]
    return ".".join(parts)


def _resolve_relative(file_module: str, is_init: bool, level: int,
                      module: str) -> str | None:
    """把相对导入转成绝对模块名。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    if level <= 0:
        return module or ""
    package = file_module.split(".") if file_module else []
    if not is_init:
        package = package[:-1]
    drop = level - 1
    if drop > len(package):
        return None
    base = package[: len(package) - drop] if drop else package
    tail = module.split(".") if module else []
    return ".".join(base + tail)


def _type_text(node: ast.AST | None) -> str | None:
    """把注解表达式转成文本。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - 极端语法
        return None


def _default_text(node: ast.AST | None) -> str | None:
    """把默认值表达式转成文本。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover
        return None


def _params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[Param]:
    """展开 arguments，保留 posonly/kwonly/vararg/kwarg 与默认值。

    @pure
    @calls: model.Param, _type_text, _default_text
    @raises: -
    @pre:
    @mutates:
    """
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults: list[ast.AST | None] = [None] * (
        len(positional) - len(args.defaults)
    ) + list(args.defaults)
    out: list[Param] = []
    for index, (arg, default) in enumerate(zip(positional, defaults)):
        kind = "posonly" if index < len(args.posonlyargs) else "pos"
        out.append(Param(arg.arg, kind, _type_text(arg.annotation),
                         _default_text(default)))
    if args.vararg is not None:
        out.append(Param(args.vararg.arg, "vararg",
                         _type_text(args.vararg.annotation), None))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        out.append(Param(arg.arg, "kwonly", _type_text(arg.annotation),
                         _default_text(default)))
    if args.kwarg is not None:
        out.append(Param(args.kwarg.arg, "kwarg",
                         _type_text(args.kwarg.annotation), None))
    return out


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef,
               params: list[Param], returns: str | None) -> str:
    """重建一行可读签名（供卡片展示，不参与编译）。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    parts: list[str] = []
    has_vararg = any(param.kind == "vararg" for param in params)
    seen_kwonly = False
    prev_kind: str | None = None
    for param in params:
        if prev_kind == "posonly" and param.kind != "posonly":
            parts.append("/")
        prev_kind = param.kind
        if param.kind == "kwonly" and not seen_kwonly and not has_vararg:
            seen_kwonly = True
            parts.append("*")
        text = param.name
        if param.kind == "vararg":
            text = "*" + text
        elif param.kind == "kwarg":
            text = "**" + text
        if param.type:
            text += f": {param.type}"
        if param.default is not None:
            text += f" = {param.default}"
        parts.append(text)
    signature = f"def {node.name}({', '.join(parts)})"
    if returns:
        signature += f" -> {returns}"
    return signature


def _body_nodes(node: ast.AST) -> list[ast.AST]:
    """收集函数体节点，但不进入嵌套函数/lambda/类（它们各自成卡）。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.Lambda, ast.ClassDef)):
            continue
        out.append(current)
        stack.extend(ast.iter_child_nodes(current))
    return out


def _call_sites(node: ast.AST) -> list[tuple[ast.Call, bool]]:
    """收集调用点；进入 lambda 体（标记 in_lambda），跳过嵌套函数/类。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    out: list[tuple[ast.Call, bool]] = []
    stack: list[tuple[ast.AST, bool]] = [
        (child, False) for child in ast.iter_child_nodes(node)
    ]
    while stack:
        current, nested = stack.pop()
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef)):
            continue
        if isinstance(current, ast.Lambda):
            stack.extend((child, True) for child in ast.iter_child_nodes(current))
            continue
        if isinstance(current, ast.Call):
            out.append((current, nested))
        stack.extend((child, nested) for child in ast.iter_child_nodes(current))
    return sorted(out, key=lambda item: item[0].lineno)


def _call_facts(node: ast.AST) -> list[CallFact]:
    """把调用点转成 CallFact（保留是否在 lambda 内）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _call_sites, _call_parts, model.CallFact
    """
    facts: list[CallFact] = []
    for call, nested in _call_sites(node):
        expr, name, receiver = _call_parts(call)
        facts.append(CallFact(name, expr, receiver, call.lineno, nested))
    return facts


def _handler_catch(handler: ast.ExceptHandler) -> tuple[bool, set[str]]:
    """返回 (是否捕获所有, 捕获的异常类型名集合)。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    if handler.type is None:
        return True, set()
    nodes = (handler.type.elts if isinstance(handler.type, ast.Tuple)
             else [handler.type])
    names: set[str] = set()
    for item in nodes:
        if isinstance(item, ast.Name):
            names.add(item.id)
        elif isinstance(item, ast.Attribute):
            names.add(item.attr)
    return False, names


def _is_caught(name: str, caught: list[set[str] | None]) -> bool:
    """判断异常是否被某一层 except 捕获（None 表示裸 except）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    for entry in caught:
        if entry is None:
            return True
        if name in entry or "Exception" in entry or "BaseException" in entry:
            return True
    return False


def _escaping_raises(node: ast.AST) -> list[RaiseFact]:
    """只收集会逃出函数的 raise（忽略被本函数 except 捕获的）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _escaping_raises.walk
    """
    found: list[RaiseFact] = []
    try_types = (ast.Try, ast.TryStar) if hasattr(ast, "TryStar") else (ast.Try,)

    def walk(current: ast.AST, caught: list[set[str] | None]) -> None:
        """递归检查语句，沿途记录已生效的 except 捕获集合。

        @pure
        @calls: _handler_catch, _escaping_raises.walk, _raise_type, _is_caught, model.RaiseFact
        @raises: -
        @pre:
        @mutates:
        """
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef, ast.Lambda)):
            return
        if isinstance(current, try_types):
            catch_all = False
            names: set[str] = set()
            for handler in current.handlers:
                all_types, handler_names = _handler_catch(handler)
                catch_all = catch_all or all_types
                names |= handler_names
            inner = caught + [None if catch_all else names]
            for statement in current.body:
                walk(statement, inner)
            for handler in current.handlers:
                walk(handler, caught)
            for statement in current.orelse:
                walk(statement, caught)
            for statement in current.finalbody:
                walk(statement, caught)
            return
        if isinstance(current, ast.Raise):
            name = _raise_type(current.exc)
            if name and not _is_caught(name, caught):
                found.append(RaiseFact(name, current.lineno))
            return
        for child in ast.iter_child_nodes(current):
            walk(child, caught)

    for statement in getattr(node, "body", []):
        walk(statement, [])
    return sorted(found, key=lambda item: item.line)


def _global_uses(node: ast.AST) -> list[dict[str, str]]:
    """收集 global/nonlocal 变量的读写（去重，按名字排序）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _body_nodes
    """
    declared: dict[str, str] = {}
    for current in _body_nodes(node):
        if isinstance(current, ast.Global):
            for name in current.names:
                declared[name] = "global"
        elif isinstance(current, ast.Nonlocal):
            for name in current.names:
                declared[name] = "nonlocal"
    if not declared:
        return []
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for current in _body_nodes(node):
        if not isinstance(current, ast.Name) or current.id not in declared:
            continue
        kind = "write" if isinstance(current.ctx, (ast.Store, ast.Del)) else "read"
        key = (current.id, kind)
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": current.id, "scope": declared[current.id],
                    "kind": kind})
    return sorted(out, key=lambda item: (item["name"], item["kind"]))


def _has_yield(node: ast.AST) -> bool:
    """函数体内是否有 yield/yield from（不含嵌套函数）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _body_nodes
    """
    return any(isinstance(item, (ast.Yield, ast.YieldFrom))
               for item in _body_nodes(node))


def _is_classvar(annotation: ast.expr | None) -> bool:
    """注解是否是 ClassVar。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _type_text
    """
    text = _type_text(annotation) or ""
    return text.startswith("ClassVar") or text.startswith("typing.ClassVar")


def _class_fields(node: ast.ClassDef) -> list[dict[str, object]]:
    """抽取类体上的字段：名字、类型、默认值、是否 ClassVar。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _type_text, _default_text, _is_classvar
    """
    fields: list[dict[str, object]] = []
    for statement in node.body:
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            fields.append({
                "name": statement.target.id,
                "type": _type_text(statement.annotation),
                "default": _default_text(statement.value),
                "classvar": _is_classvar(statement.annotation),
            })
        elif isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("__"):
                    fields.append({
                        "name": target.id,
                        "type": None,
                        "default": _default_text(statement.value),
                        "classvar": False,
                    })
    return fields


def _exports(tree: ast.Module) -> list[str]:
    """读取模块级 `__all__` 的字符串列表。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__"
                   for target in statement.targets):
            continue
        if not isinstance(statement.value, (ast.List, ast.Tuple)):
            continue
        return [item.value for item in statement.value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)]
    return []


def _param_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """函数参数名（不含 self/cls）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    args = node.args
    names = [item.arg for item in list(args.posonlyargs) + list(args.args)]
    names += [item.arg for item in args.kwonlyargs]
    if args.vararg is not None:
        names.append(args.vararg.arg)
    if args.kwarg is not None:
        names.append(args.kwarg.arg)
    return [name for name in names if name not in ("self", "cls")]


def _mutated_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """抽取被函数体改写的参数（属性/下标/删除/增量赋值）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _param_names, _body_nodes
    """
    names = set(_param_names(node))
    mutated: set[str] = set()
    for child in _body_nodes(node):
        targets: list[ast.expr] = []
        if isinstance(child, ast.Assign):
            targets = list(child.targets)
        elif isinstance(child, (ast.AugAssign, ast.AnnAssign)):
            targets = [child.target]
        elif isinstance(child, ast.Delete):
            targets = list(child.targets)
        for target in targets:
            if not isinstance(target, (ast.Attribute, ast.Subscript)):
                continue  # 仅重绑定参数名不算改写对象
            root: ast.AST = target
            while isinstance(root, (ast.Attribute, ast.Subscript)):
                root = root.value
            if isinstance(root, ast.Name) and root.id in names:
                mutated.add(root.id)
    rebound = {child.id for child in _body_nodes(node)
               if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)}
    for child in _body_nodes(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in _MUTATING_METHODS:
            continue
        root = func.value
        while isinstance(root, (ast.Attribute, ast.Subscript)):
            root = root.value
        if (isinstance(root, ast.Name) and root.id in names
                and root.id not in rebound):
            mutated.add(root.id)
    return sorted(mutated)


def _collect_local_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """收集函数内的局部名字（参数、赋值、except 别名）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _param_names, _body_nodes
    """
    names = set(_param_names(node))
    for child in _body_nodes(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            names.add(child.id)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            names.add(child.name)
    return names


def _ref_context(parent: ast.AST | None) -> str:
    """判断引用出现的语境（传参/赋值/容器/返回/其它）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    if isinstance(parent, (ast.Call, ast.keyword)):
        return "argument"
    if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return "alias"
    if isinstance(parent, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        return "container"
    if isinstance(parent, ast.Return):
        return "return"
    return "value"


def _in_annotation(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
    """节点是否位于类型注解内（注解不算函数引用）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    current: ast.AST = node
    for _ in range(10):
        parent = parents.get(id(current))
        if parent is None:
            return False
        if isinstance(parent, ast.AnnAssign) and parent.annotation is current:
            return True
        if isinstance(parent, ast.arg) and parent.annotation is current:
            return True
        current = parent
    return False


def _reference_sites(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict]:
    """抽取“函数被当值使用”的位置（回调、注册、赋值、容器）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _body_nodes, _collect_local_names, _in_annotation, _ref_context
    """
    body = _body_nodes(node)
    parents: dict[int, ast.AST] = {}
    for parent in body:
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    local = _collect_local_names(node)
    out: list[dict] = []
    for current in body:
        if not isinstance(current, (ast.Name, ast.Attribute)):
            continue
        if not isinstance(current.ctx, ast.Load):
            continue
        parent = parents.get(id(current))
        if isinstance(parent, ast.Call) and parent.func is current:
            continue  # 直接调用，不是引用
        if isinstance(parent, ast.Attribute) and parent.value is current:
            continue  # 只取最外层属性链
        if isinstance(parent, ast.Subscript) and parent.value is current:
            continue
        if _in_annotation(current, parents):
            continue
        if isinstance(current, ast.Name):
            if current.id in local or current.id in ("self", "cls"):
                continue
            name = current.id
        else:
            name = current.attr
        out.append({
            "name": name,
            "expr": " ".join(ast.unparse(current).split()),
            "line": current.lineno,
            "context": _ref_context(parent),
        })
    return sorted(out, key=lambda item: item["line"])


_MUTATING_METHODS = {
    "append", "extend", "insert", "remove", "pop", "clear", "sort",
    "update", "setdefault", "popitem", "add", "discard", "write",
    "writelines", "seek", "truncate",
}


def _literal_arg(call: ast.Call, index: int) -> str | None:
    """取第 index 个位置参数（或 mode=）的字符串字面量。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    if index < len(call.args):
        item = call.args[index]
        if isinstance(item, ast.Constant) and isinstance(item.value, str):
            return item.value
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            if isinstance(value, str):
                return value
    return None


def _classify_effects(call: ast.Call) -> list[tuple[str, str | None]]:
    """根据调用表达式判断可识别的效果种类。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _literal_arg
    """
    func = call.func
    if isinstance(func, ast.Name):
        name = func.id
        if name == "open":
            mode = _literal_arg(call, 1) or "r"
            kind = "file.write" if any(flag in mode for flag in "wax+") else "file.read"
            return [(kind, _literal_arg(call, 0))]
        if name == "print":
            return [("log", None)]
        if name == "input":
            return [("ui", None)]
        if name in ("exit", "quit"):
            return [("process", None)]
        if name in ("eval", "exec"):
            return [("state", None)]
        return []

    if not isinstance(func, ast.Attribute):
        return []
    attr = func.attr
    try:
        receiver = ast.unparse(func.value)
    except Exception:  # pragma: no cover
        receiver = ""
    tail = receiver.rsplit(".", 1)[-1]
    out: list[tuple[str, str | None]] = []
    if attr in ("write_text", "write_bytes"):
        out.append(("file.write", None))
    elif attr in ("read_text", "read_bytes"):
        out.append(("file.read", None))
    elif attr == "open":
        mode = _literal_arg(call, 0) or "r"
        out.append(("file.write" if any(flag in mode for flag in "wax+")
                    else "file.read", None))
    elif attr in ("info", "debug", "warning", "error", "critical", "exception") \
            and (tail in ("logging", "logger") or "log" in receiver.lower()):
        out.append(("log", None))
    elif attr == "getenv" and tail == "os":
        out.append(("env", None))
    elif attr in ("get", "setdefault") and receiver.endswith("environ"):
        out.append(("env", None))
    elif attr in ("now", "today", "utcnow") and "datetime" in receiver:
        out.append(("clock", None))
    elif attr in ("time", "monotonic", "perf_counter") and tail == "time":
        out.append(("clock", None))
    elif tail == "random":
        out.append(("random", None))
    elif attr in ("run", "call", "check_call", "check_output", "Popen",
                  "system", "popen") and tail in ("subprocess", "os"):
        out.append(("process", None))
    elif attr in ("execute", "executemany"):
        sql = (_literal_arg(call, 0) or "").strip().lower()
        out.append(("db.read" if sql.startswith("select") else "db.write", None))
    elif attr in ("commit", "rollback"):
        out.append(("db.write", None))
    elif attr in ("fetchone", "fetchall", "fetchmany"):
        out.append(("db.read", None))
    elif tail in ("requests", "httpx") or (attr in ("get", "post", "put", "delete")
                                           and tail == "session"):
        out.append(("net", None))
    return out


def _effect_sources(node: ast.AST) -> list[dict[str, object]]:
    """扫描函数体里可识别的效果源，返回 kind/target/line/expr。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _body_nodes, _classify_effects
    """
    out: list[dict[str, object]] = []
    seen: set[tuple[str, int]] = set()
    for child in _body_nodes(node):
        if not isinstance(child, ast.Call):
            continue
        for kind, target in _classify_effects(child):
            key = (kind, child.lineno)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "kind": kind,
                "target": target,
                "line": child.lineno,
                "expr": " ".join(ast.unparse(child.func).split()),
            })
    return sorted(out, key=lambda item: (item["line"], item["kind"]))


def _param_annotations(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    """收集参数名到注解文本的映射。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    out: dict[str, str] = {}
    for arg in list(node.args.args) + list(node.args.kwonlyargs):
        if arg.annotation is not None:
            out[arg.arg] = " ".join(ast.unparse(arg.annotation).split())
    return out


_BINOP_DUNDERS = {
    ast.Add: "__add__", ast.Sub: "__sub__", ast.Mult: "__mul__",
    ast.Div: "__truediv__", ast.FloorDiv: "__floordiv__", ast.Mod: "__mod__",
    ast.Eq: "__eq__", ast.NotEq: "__ne__", ast.Lt: "__lt__",
    ast.LtE: "__le__", ast.Gt: "__gt__", ast.GtE: "__ge__",
}


def _protocol_sites(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict]:
    """抽取隐式协议调用点（property/with/for/运算符/__call__）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _param_annotations, _body_nodes, _protocol_sites.add
    """
    ann = _param_annotations(node)
    sites: list[dict] = []

    def add(kind: str, expr: str, line: int, annotation: str = "",
            attr: str = "", dunder: str = "") -> None:
        """追加一个协议调用点。

        @pure
        @calls: -
        @raises: -
        @pre:
        @mutates: sites
        """
        sites.append({"kind": kind, "expr": expr, "line": line,
                      "annotation": annotation, "attr": attr, "dunder": dunder})

    for child in _body_nodes(node):
        if isinstance(child, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = (list(child.targets) if isinstance(child, ast.Assign)
                       else [child.target])
            for target in targets:
                if not isinstance(target, ast.Attribute):
                    continue
                root = target.value
                if isinstance(root, ast.Name) and root.id in ("self", "cls"):
                    add("property", f"{root.id}.{target.attr}", child.lineno,
                        attr=target.attr)
                elif isinstance(root, ast.Name) and root.id in ann:
                    add("property", root.id, child.lineno, ann[root.id],
                        attr=target.attr)
        elif isinstance(child, (ast.With, ast.AsyncWith)):
            for item in child.items:
                expression = " ".join(ast.unparse(item.context_expr).split())
                annotation = ""
                if isinstance(item.context_expr, ast.Name):
                    annotation = ann.get(item.context_expr.id, "")
                add("with", expression, child.lineno, annotation)
        elif isinstance(child, (ast.For, ast.AsyncFor)):
            expression = " ".join(ast.unparse(child.iter).split())
            annotation = ""
            if isinstance(child.iter, ast.Name):
                annotation = ann.get(child.iter.id, "")
            add("for", expression, child.lineno, annotation)
        elif isinstance(child, (ast.BinOp, ast.Compare)):
            operands = ([child.left, child.right] if isinstance(child, ast.BinOp)
                        else [child.left] + list(child.comparators))
            dunder = (_BINOP_DUNDERS.get(type(child.op), "")
                      if isinstance(child, ast.BinOp)
                      else _BINOP_DUNDERS.get(type(child.ops[0]), ""))
            if not dunder:
                continue
            for operand in operands:
                if isinstance(operand, ast.Name) and operand.id in ann:
                    add("binop", operand.id, child.lineno, ann[operand.id],
                        dunder=dunder)
        elif isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            if child.func.id in ann:
                add("call", child.func.id, child.lineno, ann[child.func.id])
    return sorted(sites, key=lambda item: item["line"])


def _env_names(node: ast.AST) -> list[str]:
    """抽取 os.environ/os.getenv 使用的环境变量名。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _body_nodes
    """
    names: set[str] = set()
    for child in _body_nodes(node):
        if isinstance(child, ast.Subscript):
            value = child.value
            if (isinstance(value, ast.Attribute) and value.attr == "environ"
                    and isinstance(value.value, ast.Name) and value.value.id == "os"
                    and isinstance(child.slice, ast.Constant)
                    and isinstance(child.slice.value, str)):
                names.add(child.slice.value)
        elif isinstance(child, ast.Call):
            func = child.func
            if not isinstance(func, ast.Attribute) or not child.args:
                continue
            first = child.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            receiver = ""
            try:
                receiver = ast.unparse(func.value)
            except Exception:  # pragma: no cover
                receiver = ""
            if func.attr == "getenv" and receiver == "os":
                names.add(first.value)
            elif func.attr == "get" and receiver.endswith("environ"):
                names.add(first.value)
    return sorted(names)


def _entry_decorators(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """记录函数上的装饰器（用于识别框架入口）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    return [" ".join(ast.unparse(item).split()) for item in node.decorator_list]


def _asserts(node: ast.AST) -> list[dict[str, object]]:
    """抽取函数体内的 assert 语句（行号 + 条件文本）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _body_nodes
    """
    out: list[dict[str, object]] = []
    for child in _body_nodes(node):
        if isinstance(child, ast.Assert):
            out.append({"line": child.lineno,
                        "condition": ast.unparse(child.test)})
    return out


def _is_overload(node: ast.AST) -> bool:
    """是否 @overload 声明（装饰器名以 overload 结尾）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    for decorator in getattr(node, "decorator_list", []):
        try:
            text = ast.unparse(decorator)
        except Exception:  # pragma: no cover
            continue
        if text.rsplit(".", 1)[-1].startswith("overload"):
            return True
    return False


def _definition_calls(node: ast.AST) -> list[CallFact]:
    """收集装饰器与默认值里的调用（定义期执行）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _call_facts
    """
    facts: list[CallFact] = []
    for decorator in getattr(node, "decorator_list", []):
        facts.extend(_call_facts(decorator))
    args = getattr(node, "args", None)
    if args is not None:
        defaults = list(args.defaults) + [
            item for item in args.kw_defaults if item is not None
        ]
        for default in defaults:
            facts.extend(_call_facts(default))
    return sorted(facts, key=lambda item: item.line)


def _is_main_guard(statement: ast.stmt) -> bool:
    """判断是否是 `if __name__ == "__main__":` 块。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    if not isinstance(statement, ast.If):
        return False
    test = statement.test
    if not isinstance(test, ast.Compare) or not isinstance(test.left, ast.Name):
        return False
    if test.left.id != "__name__":
        return False
    return any(isinstance(item, ast.Constant) and item.value == "__main__"
               for item in test.comparators)


def _body_definition_calls(body: list[ast.stmt]) -> list[CallFact]:
    """模块/类体内、非定义语句中的调用（import/类创建时执行）。

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: _is_main_guard, _call_facts
    """
    facts: list[CallFact] = []
    for statement in body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
            continue
        if _is_main_guard(statement):
            continue
        facts.extend(_call_facts(statement))
    return sorted(facts, key=lambda item: item.line)


def _raise_type(exc: ast.expr | None) -> str | None:
    """取 raise 的异常类型名；裸 raise 返回 None。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    if exc is None:
        return None
    if isinstance(exc, ast.Call):
        exc = exc.func
    if isinstance(exc, ast.Name):
        return exc.id
    if isinstance(exc, ast.Attribute):
        return exc.attr
    return None


def _call_parts(node: ast.Call) -> tuple[str, str, str | None]:
    """返回 (expr, name, receiver)。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    func = node.func
    expr = " ".join(ast.unparse(func).split())
    if isinstance(func, ast.Name):
        return expr, func.id, None
    if isinstance(func, ast.Attribute):
        receiver = " ".join(ast.unparse(func.value).split())
        return expr, func.attr, receiver
    tail = [part for part in expr.replace("(", " ").replace(")", " ").split()
            if part.isidentifier()]
    return expr, tail[-1] if tail else expr, None


def _purity_violations(node: ast.AST) -> list[str]:
    """收集 @pure 冲突源：global/nonlocal、yield、IO 调用、进程退出。

    @pure
    @calls: _body_nodes
    @raises: -
    @pre:
    @mutates:
    """
    found: list[str] = []
    for child in _body_nodes(node):
        if isinstance(child, (ast.Global, ast.Nonlocal)):
            found.append("global/nonlocal")
        if isinstance(child, (ast.Yield, ast.YieldFrom)):
            found.append("yield")
        if isinstance(child, ast.Raise):
            exc = child.exc
            if isinstance(exc, ast.Call):
                exc = exc.func
            if isinstance(exc, ast.Name) and exc.id == "SystemExit":
                found.append("raise SystemExit")
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name) and func.id in _PURE_BUILTINS | {"exit", "quit"}:
                found.append(f"{func.id}()")
            if (isinstance(func, ast.Attribute) and func.attr == "exit"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "sys"):
                found.append("sys.exit()")
    return sorted(set(found))


def _mutable_defaults(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """检测可变默认参数（字面量或 list()/dict()/set() 调用）。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    names: list[str] = []
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults: list[ast.AST | None] = [None] * (
        len(positional) - len(args.defaults)
    ) + list(args.defaults)
    pairs = list(zip(positional, defaults))
    pairs += list(zip(args.kwonlyargs, args.kw_defaults))
    for arg, default in pairs:
        if isinstance(default, (ast.List, ast.Dict, ast.Set)):
            names.append(arg.arg)
        elif (isinstance(default, ast.Call) and isinstance(default.func, ast.Name)
              and default.func.id in _MUTABLE_CALLS and not default.args):
            names.append(arg.arg)
    return names


def _decorators(node: ast.AST) -> list[str]:
    """取装饰器文本列表。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    out: list[str] = []
    for decorator in getattr(node, "decorator_list", []):
        try:
            out.append(" ".join(ast.unparse(decorator).split()))
        except Exception:  # pragma: no cover
            continue
    return out


def _hash(lines: list[str], start: int, end: int) -> str:
    """对 1-based 闭区间的源码行做 sha1。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    text = "\n".join(lines[start - 1:end])
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class _Extractor:
    """单文件抽取器：维护词法作用域与已产出的卡片。

    """

    def __init__(self, source: str, rel_path: str, module: str) -> None:
        """保存源码与文件元数据。

        @pure
        @calls: -
        @raises: -
        @pre:
        @mutates:
        """
        self.source = source
        self.lines = source.splitlines()
        self.rel = rel_path
        self.module = module
        self.is_init = rel_path.endswith("__init__.py")
        self.cards: list[Card] = []
        self.imports: list[ImportFact] = []

    def run(self, tree: ast.Module) -> FileFacts:
        """抽取模块卡片、符号卡片与 import。

        @effect: state
        @calls: _Extractor._module_card, _Extractor._visit, _Extractor._collect_imports, FileFacts
        @raises: -
        @pre:
        @mutates:
        """
        self.cards.append(self._module_card(tree))
        for statement in tree.body:
            self._visit(statement, [], None, False)
        self._collect_imports(tree)
        return FileFacts(self.cards, self.imports)

    def _docstring(self, node: ast.AST) -> tuple[str, int]:
        """返回 (docstring 文本, 起始行)。

        @pure
        @calls: -
        @raises: -
        @pre:
        @mutates:
        """
        body = getattr(node, "body", [])
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            return body[0].value.value, body[0].lineno
        return "", getattr(node, "lineno", 1)

    def _module_card(self, tree: ast.Module) -> Card:
        """构造模块卡片。

        @pure
        @calls: _Extractor._docstring, tags.parse_tags, _split_legacy, model.Card, tags.summary_of, _body_definition_calls, _exports
        @raises: -
        @pre:
        @mutates:
        """
        doc, doc_line = self._docstring(tree)
        tags, tag_lines = parse_tags(doc, doc_line)
        tags, legacy, tag_lines = _split_legacy(tags, tag_lines)
        return Card(
            kind="module", file=self.rel, module=self.module,
            qname=self.module,
            name=self.module.split(".")[-1] if self.module else "",
            start_line=1, end_line=max(len(self.lines), 1),
            summary=summary_of(doc), tags=tags, legacy_tags=legacy,
            tag_lines=tag_lines,
            definition_calls=_body_definition_calls(tree.body),
            exports=_exports(tree),
            doc_sha1=hashlib.sha1(doc.encode("utf-8")).hexdigest(),
        )

    def _class_card(self, node: ast.ClassDef, scope: list[str]) -> Card:
        """构造类卡片（只承载摘要与标签）。

        @pure
        @calls: _Extractor._docstring, tags.parse_tags, _split_legacy, model.Card, tags.summary_of, _decorators, _class_fields, _body_definition_calls, _hash
        @raises: -
        @pre:
        @mutates:
        """
        doc, doc_line = self._docstring(node)
        tags, tag_lines = parse_tags(doc, doc_line)
        tags, legacy, tag_lines = _split_legacy(tags, tag_lines)
        qname = ".".join(scope + [node.name])
        end = node.end_lineno or node.lineno
        bases = [" ".join(ast.unparse(item).split()) for item in node.bases]
        return Card(
            kind="class", file=self.rel, module=self.module, qname=qname,
            name=node.name, start_line=node.lineno, end_line=end,
            decorators=_decorators(node), summary=summary_of(doc),
            tags=tags, legacy_tags=legacy, tag_lines=tag_lines,
            bases=bases, fields=_class_fields(node),
            definition_calls=_body_definition_calls(node.body),
            body_sha1=_hash(self.lines, node.lineno, end),
            doc_sha1=hashlib.sha1(doc.encode("utf-8")).hexdigest(),
        )

    def _function_card(self, node: ast.FunctionDef | ast.AsyncFunctionDef,
                       scope: list[str], kind: str) -> Card:
        """构造函数/方法卡片：参数、返回、raise、调用、纯度信号。

        @pure
        @calls: _Extractor._docstring, tags.parse_tags, _split_legacy, _params, _type_text, _decorators, model.Card, _signature, tags.summary_of, _call_facts, _escaping_raises, _definition_calls, _global_uses, _reference_sites, _mutated_params, _effect_sources, _protocol_sites, _env_names, _entry_decorators, _asserts, _has_yield, _is_overload, _purity_violations, _mutable_defaults, _hash
        @raises: -
        @pre:
        @mutates:
        """
        doc, doc_line = self._docstring(node)
        tags, tag_lines = parse_tags(doc, doc_line)
        tags, legacy, tag_lines = _split_legacy(tags, tag_lines)
        params = _params(node)
        returns = _type_text(node.returns)
        decorators = _decorators(node)
        qname = ".".join(scope + [node.name])
        for decorator in decorators:
            tail = decorator.rsplit(".", 1)[-1]
            if tail in ("getter", "setter", "deleter"):
                qname = f"{qname}.{tail}"
                break
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        end = node.end_lineno or node.lineno
        return Card(
            kind=kind, file=self.rel, module=self.module, qname=qname,
            name=node.name, start_line=start, end_line=end,
            def_line=node.lineno,
            signature=_signature(node, params, returns), params=params,
            returns=returns, decorators=decorators, summary=summary_of(doc),
            tags=tags, legacy_tags=legacy, tag_lines=tag_lines,
            raises=_escaping_raises(node), calls=_call_facts(node),
            definition_calls=_definition_calls(node),
            globals=_global_uses(node),
            references=_reference_sites(node),
            mutated_params=_mutated_params(node),
            effect_sources=_effect_sources(node),
            protocol_sites=_protocol_sites(node),
            env_names=_env_names(node),
            entry_decorators=_entry_decorators(node),
            asserts=_asserts(node),
            is_generator=_has_yield(node),
            is_overload=_is_overload(node),
            purity_violations=_purity_violations(node),
            mutable_defaults=_mutable_defaults(node),
            body_sha1=_hash(self.lines, node.lineno, end),
            doc_sha1=hashlib.sha1(doc.encode("utf-8")).hexdigest(),
        )

    def _visit(self, node: ast.AST, scope: list[str], cls: str | None,
               in_class_body: bool) -> None:
        """递归发现定义：函数、方法、类，以及它们内部的嵌套定义。

        @effect: state
        @calls: _Extractor._function_card, _Extractor._visit, _Extractor._class_card
        @raises: -
        @pre:
        @mutates:
        """
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "method" if in_class_body else "function"
            card = self._function_card(node, scope, kind)
            self.cards.append(card)
            for child in node.body:
                self._visit(child, scope + [node.name], cls, False)
            return
        if isinstance(node, ast.ClassDef):
            card = self._class_card(node, scope)
            self.cards.append(card)
            for child in node.body:
                self._visit(child, scope + [node.name], card.qname, True)
            return
        for child in ast.iter_child_nodes(node):
            self._visit(child, scope, cls, False)

    def _collect_imports(self, tree: ast.Module) -> None:
        """收集全文件 import，并把相对导入转成绝对模块名。

        @effect: state
        @calls: model.ImportFact, _resolve_relative
        @raises: -
        @pre:
        @mutates:
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        bound = alias.asname
                        module = alias.name
                    else:
                        bound = alias.name.split(".")[0]
                        module = bound
                    self.imports.append(ImportFact(
                        "module", module, 0, None, bound, node.lineno))
            elif isinstance(node, ast.ImportFrom):
                absolute = _resolve_relative(
                    self.module, self.is_init, node.level, node.module or "")
                if absolute is None:
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        self.imports.append(ImportFact(
                            "wildcard", absolute, node.level, None, None,
                            node.lineno))
                        continue
                    self.imports.append(ImportFact(
                        "from", absolute, node.level, alias.name,
                        alias.asname or alias.name, node.lineno))


def extract_file(path: str, rel_path: str) -> FileFacts:
    """抽取单个 .py 文件；语法/编码错误转成 SourceError。

    @effect: file.read
    @raises: SourceError 当文件无法读取或解析
    @calls: read_source, SourceError, _Extractor, module_name
    @pre:
    @mutates:
    """
    source = read_source(path)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise SourceError(f"语法错误: {exc.msg}（第 {exc.lineno} 行）") from exc
    extractor = _Extractor(source, rel_path, module_name(rel_path))
    return extractor.run(tree)
