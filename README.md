# code-standard-python

> 事实由 AST 生成，意图只写一遍；函数里能看到调用关系，但它由工具写、由工具校验。

## 为什么有 v2

v1 把调用关系写进 docstring（`@calls` / `@called-by`），但靠人手维护：
实测示范项目 `logistics`（161 个函数、345 条 `@calls`）里，**10 条声明根本没被调用**、
**18 个函数漏写内部调用**，而 v1 的 `lint.py` 报告“全部通过”。

v2 的取舍：

| 信息 | 谁产生 | 存在哪 |
|---|---|---|
| 签名、参数、返回、调用边、raise 点 | AST（`csp sync`） | 卡片 + **写回 docstring 的 `@calls`** |
| 副作用（结构化） | 人/模型写 docstring | `@effect: db.write(claims), log` |
| 纯度、异常、前置条件、线程安全、废弃 | 人/模型写 docstring | `@pure` / `@raises` / `@pre` 等 |
| 两者是否一致 | `csp check` | C0–C22 |
| 项目开关 / 文字约定 | `csp init` | `.code-standard.json` / `AGENTS.md` |

`@calls` 不再手写：`csp sync --write` 按 AST 生成并回填；`csp check` 比对，
手改或多写一条就报 C21。这样函数是自解释的，而事实不会漂移。

## 安装

```bash
cd code-standard-python
python -m venv .venv
.venv/Scripts/python -m pip install -e .     # Windows
# .venv/bin/python -m pip install -e .       # POSIX
```

无第三方依赖（只用标准库 `ast`），Python ≥ 3.10。

## 快速开始

```bash
csp init --root path/to/repo       # 写 .code-standard.json + AGENTS.md
csp brief --file path/to/x.py      # 生成上下文：规则 + 约定 + 现有符号/调用
csp sync  --root path/to/repo --write   # 卡片 + 回填 @calls
csp check --root path/to/repo      # 对账，error 时退出码 1
csp check --root path/to/repo --types   # 额外跑 mypy，类型错误记为 T1
csp check --root path/to/repo --staged --strict   # pre-commit 用
csp prompt / csp rules / csp stats # 提示、规则表、统计
csp db build --root path/to/repo  # SQLite 索引（.code-standard/index.db）
csp query callers NAME --root path/to/repo   # callers/callees/impact/raises/effects/symbol
```

`csp brief` 是“生成标准化”的入口：把当次任务需要的规则、项目约定、
目标文件现有符号与调用拼成一段上下文（默认约 1k token，`--no-prompt` 只留事实）。

项目策略只放机器开关：

```json
{"schema": 1, "strict": false, "skip_dirs": ["migrations"]}
```

文字约定写在 `AGENTS.md`（`csp init` 生成，`brief` 原样注入）；README 面向人。

## 代码长什么样

```python
"""报价服务：订单 → 应付金额（单位：分）。

@stable
"""
from __future__ import annotations


def coupon_value(coupon: str | None) -> int:
    """券面额；无券为 0。

    @pure
    @raises: ValueError 当券码不以 C 开头
    @pre:
    @mutates:
    @calls: -
    """
    if coupon is None:
        return 0
    if not coupon.startswith("C"):
        raise ValueError(f"非法券码 {coupon}")
    return int(coupon[1:] or 0)


def save_quote(path: str, total: int) -> None:
    """把总额写入文件。

    @effect: file.write(path)
    @pre: path 的父目录存在
    @mutates:
    @raises: OSError 当目录不可写
    @calls: -
    """
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(total))
```

- `@calls` 只写项目内、精确解析到的调用（跨文件带模块前缀）；外部包/builtin/
  未解析目标不写。没有内部调用时是 `@calls: -`。这一行由 `csp sync --write`
  维护，**不要手改**。
- 每个函数（含嵌套函数）都有五个固定槽位：`@effect`（或 `@pure`）、`@calls`、`@raises`、
  `@pre`、`@mutates`。没有内容写 `-`，未确认可留空；缺失的槽位由
  `csp sync --write` 自动补齐（单行 docstring 会被展开）。
- `@effect` 是枚举，不是自由文本：`db.read` / `db.write` / `file.read` /
  `file.write` / `net` / `log` / `env` / `clock` / `random` / `process` /
  `state` / `ui`；`none` 等价 `@pure`。

## 卡片：跨函数的全局信息

`csp sync` 还生成 `.code-standard/cards.jsonl`，每张卡记录完整调用边与反向 `called_by`：

```json
"calls": [
  {"name": "save_claim", "expr": "store.save_claim", "line": 950,
   "confidence": "exact",
   "target": {"qname": "save_claim", "file": "store.py", "line": 477}}
],
"called_by": [{"qname": "_h_pay", "file": "main.py", "line": 464, "confidence": "exact"}]
```

函数里写的是**自己调谁**（局部、稳定）；**谁调我**是全局属性，随任何调用方改动而变，
不写进 docstring，只在卡片里。查询影响面时用 `called_by`。

## 规则（摘要）

必填：摘要、type hints、`@pure` 或 `@effect`、有 raise 时的 `@raises`、`@calls`（工具生成）。
选填：`@pre`、`@mutates`、`@env`、`@pii`、`@idempotent`、`@transactional`、
`@thread-safe` / `@not-thread-safe`、`@deprecated`、`@invariant`。
模块：摘要 + `@stable` / `@experimental`（严格模式必填）。

完整规则表（C0–C22）见 [standard.md](standard.md)，或运行 `csp rules`。

## 迁移 v1

```bash
csp sync --root . --write    # 用事实重写所有 @calls（假声明/漏写一次修好）
csp check --root .           # 剩下的旧 @side-effect 会报 C20 warning
```

步骤：

1. `sync --write` 重写 `@calls`，不再手写；
2. 把 `@side-effect: <描述>` 改成 `@effect: <种类>(<目标>)`（C20 会逐个提示）；
3. 删除 `@called-by` / `@entry` / `@depends`（C15 提示，关系在卡片）；
4. `csp check --strict` 接入 CI。

## 作为 Skill 使用

把 `skill/code-standard/` 复制到 harness 的技能目录。Skill 只做编排：
`csp brief` 拿上下文、`sync --write` 回填 `@calls`、`check` 对账；规则文本不复制进 Skill。

## 接入 git

```bash
cp hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

钩子只校验暂存文件（`csp check --staged --strict`），CI 再跑全量 `--strict` 兜底。

## 结构

```
code-standard-python/
├── standard.md                  # 规范：标签、卡片格式、规则表
├── skill/code-standard/         # SKILL：闭环与验收门
├── hooks/pre-commit             # 暂存文件校验
├── examples/pricing/            # 按 v2 写成的示例模块
├── src/code_standard_v2/
│   ├── model.py                 # Card / Param / CallFact / RaiseFact
│   ├── tags.py                  # 标签、效果枚举、解析
│   ├── config.py                # .code-standard.json 与 AGENTS.md
│   ├── extract.py               # AST → 卡片（事实层）
│   ├── resolve.py               # 调用解析与置信度
│   ├── inline.py                # 生成 @calls 的值
│   ├── writeback.py             # 把 @calls 写回 docstring
│   ├── check.py                 # C0–C22 对账规则
│   ├── rules.py                 # 规则号/级别的唯一来源
│   ├── data/prompt.md           # 生成提示（csp prompt 输出）
│   └── cli.py                   # sync / check / brief / init / prompt / rules
└── tests/test_v2.py             # 56 个 unittest
```

## 测试

```bash
.venv/Scripts/python -m unittest discover -s tests -v
```

56 个用例覆盖：参数展开、BOM、标签/效果解析、raise 对账（含被 `except` 捕获的）、
可变默认值、纯度冲突与两跳传递、方法作用域、类标签越界、内联 `@calls` 的
生成/幂等/篡改检测、lambda 调用、引用边（回调/注册）、global 写入、模块级调用、
`@overload`、继承与 `super()`、类字段、参数改写、异常传播、assert/`__all__`、
包级重导出（`from pkg import X`）、mypy 输出解析与 `--types`、效果静态检测（C27）、
git 改动识别、init 幂等、配置 skip/strict、brief 输出等。

本工具自身按 v2 编写：`csp check --root .` 输出 **0 条**，`sync --write` 幂等。

## 已知边界

**已覆盖**：普通/嵌套函数、属性 getter/setter、逃逸 `raise` 与调用链传播、
lambda 调用（`~name`）、引用边（`ref→`/`ref←`，回调与注册）、继承与 `super()`、
类字段（dataclass 等）、参数改写（C26）、
`global`/`nonlocal` 读写（C24）、模块/类定义期调用（C25）、装饰器与默认值调用、
生成器、`@overload`、`assert`、`__all__`、async。

**取舍**：模块常量不建标签；闭包只记录 `nonlocal` 声明；命名空间包与跨模块装饰器
内部行为仍可能退化为 `name_only`。

**不承诺**：`@pre`/`@thread-safe`/`@pii`/`@env` 只查格式，语义靠评审；
`@mutates` 只核对显式属性/下标改写（`arg.append` 这类方法改写查不到）；
`@effect` 可检测已登记 API（open/print/env/clock/random/process/db/net），
封装函数与 ORM 细节仍需人判断；测试代码默认不检查；只支持 Python。
卡片是派生产物，不入库（`.code-standard/` 自带 `.gitignore`）。
