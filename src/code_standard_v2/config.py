"""项目策略：`.code-standard.json` 只存“机器开关”，文字约定放 AGENTS.md。

策略不描述代码长什么样，只决定工具怎么跑：是否严格、扫描哪些目录。
人读的约定（金额用分、日志走封装）写在 AGENTS.md，由 brief 原样带给模型。

@stable
@calls: -
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

CONFIG_NAME = ".code-standard.json"
AGENTS_NAME = "AGENTS.md"
AGENTS_MARKER = "<!-- code-standard:v2 -->"


@dataclass
class Config:
    """一个项目的工具开关。"""

    schema: int = 1
    strict: bool = False
    skip_dirs: list[str] = field(default_factory=list)


def config_path(root: str) -> str:
    """配置文件路径。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return os.path.join(root, CONFIG_NAME)


def load(root: str) -> tuple[Config, str | None]:
    """读取配置；缺失返回默认值，损坏返回默认值 + 错误说明。

    @effect: file.read
    @calls: config_path, Config
    @raises: -
    @pre:
    @mutates:
    """
    path = config_path(root)
    if not os.path.exists(path):
        return Config(), None
    try:
        with open(path, encoding="utf-8-sig") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return Config(), f"配置无法解析（{exc}），已使用默认值"
    if not isinstance(raw, dict):
        return Config(), "配置必须是 JSON 对象，已使用默认值"
    config = Config()
    if isinstance(raw.get("strict"), bool):
        config.strict = raw["strict"]
    if isinstance(raw.get("skip_dirs"), list):
        config.skip_dirs = [str(item) for item in raw["skip_dirs"] if str(item).strip()]
    return config, None


def save(root: str, config: Config) -> str:
    """写入配置文件。

    @effect: file.write
    @calls: config_path
    @raises: -
    @pre:
    @mutates:
    """
    path = config_path(root)
    payload = {
        "schema": config.schema,
        "strict": config.strict,
        "skip_dirs": config.skip_dirs,
    }
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def agents_snippet() -> str:
    """给 AGENTS.md 追加的说明块（文字约定由项目自行补充）。

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return (
        f"{AGENTS_MARKER}\n"
        "## Python 代码规范（code-standard-python）\n\n"
        "- 写或改 Python 代码前，运行 `csp brief --file <目标文件>`；\n"
        "- docstring 只写意图（`@pure`/`@effect` 等）；**不要手写或手改** `@calls`，\n"
        "  它由 `csp sync --write` 生成；\n"
        "- 完成前依次运行 `csp sync --write`、`csp check --root . --changed`，0 error 才算完成；\n"
        "- 提交前由钩子运行 `csp check --staged --strict`。\n\n"
        "### 项目约定（按需补充，会被 brief 带给模型）\n\n"
        "- 例如：金额一律用 int 表示分；日志统一走 `log.info`；\n"
    )


def write_agents(root: str) -> tuple[str, bool]:
    """创建或追加 AGENTS.md，返回 (路径, 是否新增)。

    @effect: file.write, file.read
    @calls: agents_snippet
    @raises: -
    @pre:
    @mutates:
    """
    path = os.path.join(root, AGENTS_NAME)
    exists = os.path.exists(path)
    current = ""
    if exists:
        with open(path, encoding="utf-8-sig") as handle:
            current = handle.read()
        if AGENTS_MARKER in current:
            return path, False
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        if current and not current.endswith("\n"):
            handle.write(current + "\n\n")
        elif current:
            handle.write(current + "\n")
        handle.write(agents_snippet())
    return path, True


def read_agents(root: str, limit: int = 2000) -> str:
    """读取 AGENTS.md 的正文（超长截断），供 brief 注入。

    @effect: file.read
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    path = os.path.join(root, AGENTS_NAME)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8-sig") as handle:
        text = handle.read().strip()
    if len(text) > limit:
        text = text[:limit] + "\n…（AGENTS.md 过长，已截断）"
    return text
