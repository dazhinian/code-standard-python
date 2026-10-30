"""规则注册表：规则号、默认级别与一句话说明的唯一来源。

`cs2 rules` 直接输出本表；`tests/test_v2.py` 会断言代码里出现的规则号都在
本表内，避免规则与文档漂移。

@stable
@calls: -
"""
from __future__ import annotations

import json

RULES: dict[str, tuple[str, str]] = {
    "C0": ("error", "源码无法读取或解析（编码、语法）"),
    "C1": ("error", "模块缺少 docstring 摘要"),
    "C2": ("error", "@stable/@experimental 冲突；strict 下缺失"),
    "C3": ("warning", "类缺少 docstring 摘要"),
    "C4": ("error", "函数缺少 docstring 摘要"),
    "C5": ("error", "type hints 不完整"),
    "C6": ("error", "缺少 @pure/@effect，或两者同存"),
    "C7": ("error", "声明无副作用但函数体有副作用源"),
    "C8": ("error", "无副作用函数经调用链到达有副作用函数"),
    "C9": ("error", "有 raise 但 @raises 未声明该类型"),
    "C10": ("warning", "无调用却声明了未 raise 的异常"),
    "C11": ("error", "可变默认参数（字面量或 list()/dict()/set()）"),
    "C12": ("error", "必须有值的标签为空"),
    "C13": ("error", "@thread-safe 与 @not-thread-safe 同存"),
    "C14": ("error", "未知标签、越界标签或非法 @effect"),
    "C15": ("warning", "仍有 v1 遗留关系标签"),
    "C16": ("error", "@deprecated 函数仍被调用"),
    "C20": ("warning", "旧 @side-effect 未改成结构化 @effect"),
    "C21": ("error", "@calls 与工具生成的事实不一致"),
    "C22": ("warning", "缺少 @calls"),
    "C23": ("warning", "缺少固定槽位 @raises/@pre/@mutates"),
    "C24": ("warning", "写了 global/nonlocal 但 @effect 未声明 state"),
    "C25": ("warning", "模块/类定义期有调用（import 时执行）"),
    "C26": ("warning", "参数被改写但 @mutates 未声明"),
    "C27": ("warning", "检测到效果但 @effect 未声明"),
    "C28": ("warning", "下游效果未在本函数 @effect 声明"),
    "C29": ("warning", "使用了环境变量但 @env 未声明"),
    "T0": ("error", "类型检查器无法运行"),
    "T1": ("error", "mypy 报告的类型错误"),
}


def as_json() -> str:
    """把规则表序列化成 JSON。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return json.dumps(
        [{"rule": key, "severity": value[0], "title": value[1]}
         for key, value in RULES.items()],
        ensure_ascii=False, indent=2,
    )


def as_markdown() -> str:
    """把规则表渲染成 markdown 表格行。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    lines = ["| 规则 | 级别 | 内容 |", "|---|---|---|"]
    for key, (severity, title) in RULES.items():
        lines.append(f"| {key} | {severity} | {title} |")
    return "\n".join(lines)


def as_text() -> str:
    """把规则表渲染成终端表格。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    width = max(len(key) for key in RULES)
    lines = []
    for key, (severity, title) in RULES.items():
        lines.append(f"{key:<{width}}  {severity:<7}  {title}")
    return "\n".join(lines)
