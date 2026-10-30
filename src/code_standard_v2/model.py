"""v2 卡片模型：事实（AST 抽取）与声明（docstring 标签）分字段存放。

@stable
@calls: -
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 2
EXTRACTOR = "code-standard-v2/0.1.0"


@dataclass
class Param:
    """函数参数的一条事实。

    """

    name: str
    kind: str  # posonly | pos | kwonly | vararg | kwarg
    type: str | None
    default: str | None


@dataclass
class CallFact:
    """函数体内的一次调用（未解析）。

    """

    name: str
    expr: str
    receiver: str | None
    line: int
    in_lambda: bool = False


@dataclass
class RaiseFact:
    """函数体内的一条 raise 语句；裸 raise 的 type 为 None。

    """

    type: str | None
    line: int


@dataclass
class ImportFact:
    """文件内的一条 import（相对导入已在抽取时转成绝对模块名）。

    """

    kind: str  # module | from | wildcard
    module: str
    level: int
    name: str | None
    alias: str | None
    line: int


@dataclass
class Card:
    """一个模块/类/函数的卡片：结构、契约声明、调用边与哈希。

    """

    kind: str  # module | class | function | method
    file: str
    module: str
    qname: str
    name: str
    start_line: int
    end_line: int
    def_line: int = 0
    signature: str = ""
    params: list[Param] = field(default_factory=list)
    returns: str | None = None
    decorators: list[str] = field(default_factory=list)
    summary: str = ""
    tags: dict[str, list[str]] = field(default_factory=dict)
    legacy_tags: dict[str, list[str]] = field(default_factory=dict)
    tag_lines: dict[str, int] = field(default_factory=dict)
    raises: list[RaiseFact] = field(default_factory=list)
    calls: list[CallFact] = field(default_factory=list)
    definition_calls: list[CallFact] = field(default_factory=list)
    globals: list[dict[str, Any]] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    fields: list[dict[str, Any]] = field(default_factory=list)
    mutated_params: list[str] = field(default_factory=list)
    asserts: list[dict[str, Any]] = field(default_factory=list)
    effect_sources: list[dict[str, Any]] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    env_names: list[str] = field(default_factory=list)
    entry_decorators: list[str] = field(default_factory=list)
    propagated_raises: list[str] = field(default_factory=list)
    propagated_effects: list[str] = field(default_factory=list)
    protocol_calls: list[dict[str, Any]] = field(default_factory=list)
    protocol_sites: list[dict[str, Any]] = field(default_factory=list)
    protocol_called_by: list[dict[str, Any]] = field(default_factory=list)
    is_generator: bool = False
    is_overload: bool = False
    purity_violations: list[str] = field(default_factory=list)
    mutable_defaults: list[str] = field(default_factory=list)
    body_sha1: str = ""
    doc_sha1: str = ""
    resolved_calls: list[dict[str, Any]] = field(default_factory=list)
    called_by: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    resolved_references: list[dict[str, Any]] = field(default_factory=list)
    referenced_by: list[dict[str, Any]] = field(default_factory=list)

    def tag(self, name: str) -> str | None:
        """返回单值标签的第一个值。

        @pure
        @calls: -
        @raises: -
        @pre:
        @mutates:
        """
        values = self.tags.get(name)
        return values[0] if values else None

    def tag_values(self, name: str) -> list[str]:
        """返回标签的全部值。

        @pure
        @calls: -
        @raises: -
        @pre:
        @mutates:
        """
        return list(self.tags.get(name, []))

    def to_dict(self) -> dict[str, Any]:
        """序列化为卡片 JSON。

        @pure
        @calls: -
        @raises: -
        @pre:
        @mutates:
        """
        out: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "extractor": EXTRACTOR,
            "kind": self.kind,
            "file": self.file,
            "module": self.module,
            "qname": self.qname,
            "name": self.name,
            "lines": [self.start_line, self.end_line],
            "def_line": self.def_line,
            "signature": self.signature,
            "params": [
                {"name": p.name, "kind": p.kind, "type": p.type, "default": p.default}
                for p in self.params
            ],
            "returns": self.returns,
            "decorators": self.decorators,
            "doc": {"summary": self.summary, "tags": self.tags},
            "facts": {
                "raises_sites": [
                    {"type": r.type, "line": r.line} for r in self.raises
                ],
                "purity_violations": self.purity_violations,
                "mutable_defaults": self.mutable_defaults,
                "globals": self.globals,
                "bases": self.bases,
                "fields": self.fields,
                "mutated_params": self.mutated_params,
                "asserts": self.asserts,
                "effect_sources": self.effect_sources,
                "exports": self.exports,
                "env_names": self.env_names,
                "entry_decorators": self.entry_decorators,
                "propagated_raises": self.propagated_raises,
                "propagated_effects": self.propagated_effects,
                "protocol_calls": self.protocol_calls,
                "protocol_sites": self.protocol_sites,
                "protocol_called_by": self.protocol_called_by,
                "reference_sites": self.references,
                "is_generator": self.is_generator,
                "is_overload": self.is_overload,
                "definition_calls": [
                    {"name": c.name, "expr": c.expr, "line": c.line,
                     "in_lambda": c.in_lambda}
                    for c in self.definition_calls
                ],
            },
            "hashes": {"body_sha1": self.body_sha1, "doc_sha1": self.doc_sha1},
            "calls": self.resolved_calls,
            "called_by": self.called_by,
            "references": self.resolved_references,
            "referenced_by": self.referenced_by,
        }
        if self.legacy_tags:
            out["doc"]["legacy_tags"] = self.legacy_tags
        return out
