---
name: code-standard
description: 当任务要新增或修改 Python 代码（写模块、加函数、改逻辑、重构）且希望产出可被 csp 校验的标准代码时使用。闭环：写代码+意图标签 → csp sync --write 生成事实与 @calls → csp check 对账 → 修正。禁止手写 @calls/@called-by。
version: 2.2.0
---

# 写标准代码（code-standard-python）

## 何时用

- 新增 Python 模块、函数、类、方法；
- 修改现有函数的签名、副作用、异常或调用关系；
- 用户要求"按规范写""能被检查通过""让 AI 以后只看卡片维护"。

不适用于：只读分析、文档/配置修改、非 Python 代码。

## 前置检查

```bash
csp --help          # 没有就装：pip install -e <repo>/code-standard-python
csp rules           # 规则全表；不确定某条规则时先看它
```

新项目初始化一次（写 `.code-standard.json` 与 AGENTS.md）：

```bash
csp init --root .
```

## 四条硬纪律

1. **只写代码 + 意图标签**：摘要、type hints、`@pure` 或 `@effect`、`@raises`、按需 `@pre` 等。
2. **`@calls` 由工具写**：`csp sync --write` 生成；手写或手改会被 C21 判为不一致。
3. **绝不写** `@called-by`、`@entry`、`@depends`（遗留标签，C15 提示）。
4. **没跑过 `csp check` 不算完成**：结果（条数与退出码）写进最终答复。

## 闭环

```
① csp brief --file <目标文件>（新建用 --symbol 或省略）
   拿到：规则提示 + 项目约定 + 目标文件现有符号/调用/签名
② 写代码：docstring 只写意图（@effect 等），不写 @calls
③ csp sync --root . --write               # 生成卡片 + 回填 @calls
④ csp check --root . --paths <改动文件>   # 对账
⑤ 有 error：按 file:line 修正，回到 ③④
⑥ 汇报：改了什么、check 结果、卡片里能查到什么
```

修改已有函数时，通用模板里已有“先读原文、保留未变标签、最小 diff”的纪律，
`brief` 还会带上项目 `AGENTS.md` 里的约定。

CI 用 `csp check --root . --strict`；pre-commit 用 `csp check --root . --staged --strict`。

## 验收门（缺一不可）

- `csp check` 退出码为 0，且没有 error；
- 每个函数有摘要与 type hints；
- 有 `raise` 的函数，`@raises` 覆盖所有直接 raise 的类型；
- 声明无副作用（`@pure`/`@effect: none`）的函数，体内没有 `print`/`open`/`global`/`yield`/`sys.exit`，
  也没有经调用链到达有副作用的函数（工具追 3 跳）；
- 五个固定槽位齐全：`@effect`（或 `@pure`）、`@calls`、`@raises`、`@pre`、`@mutates`；
  没内容写 `-` 或留空，缺失的由 `csp sync --write` 补齐；
- 改写了参数属性/下标（`arg.x = ...`）时，`@mutates` 里要有这个参数（C26）；
- 函数体里的 `open`/`print`/`os.environ`/`datetime.now`/`random`/`subprocess`/
  SQL 执行，要能在 `@effect` 里找到对应种类（C27 提示）；
- `@calls` 与工具生成结果一致（跑过一次 `sync --write`）；
- 没有可变默认参数（`[]`、`{}`、`set()`、`list()`）；
- （可选）装了 mypy 时跑 `csp check --types`：它验证“注解是否相容”，
  类型错误记为 T1；没装就跳过，不要声称类型已验证。

## 读检查结果

```bash
csp check --root . --json          # 逐条处理
csp check --root . --changed       # 只看相对 HEAD 的改动
```

每条 finding 含 `file:line`、`symbol`、规则号、严重级别、修复建议。
`error` 必须修；`warning` 在 `--strict` 下等同 `error`。

## 常见失败

见 `references/failure-modes.md`：`@pure` 里写文件、漏 `@raises`、
把自由文本写进 `@effect`、可变默认参数、类标签越界、忘记 `sync --write`。

## 边界场景（见 references/scenarios.md）

- **遗留仓库**：先 `sync --write` 补事实，按文件推进，别一上来开 `--strict`；
- **动态/框架代码**：框架入口手写 `@effect`/`@pre`；`name_only` 调用必须人工核实；
- **新代码调用已有函数**：收尾前对每个项目内调用跑 `csp brief --symbol <名字>`，
  查不到卡片就是不存在；`check` 通过 ≠ 调用解析成功；
- **回调/注册**：brief 里的 `ref→` / `ref←` 是“函数被当值传出去”，不是调用；
  改这些函数的签名时要一起看引用方；
- **多 agent**：`sync --write` 串行执行；冲突后重跑，不手改 `@calls`；
- **大仓库**：日常只用 `--changed`/`--staged`，全量 `--strict` 放 CI；
- **只读/CI**：本地 `sync`，提交 `@calls`；CI 只 `check`；
- **生成代码/vendor**：先写进 `skip_dirs`，再跑 `sync`；
- **读卡片**：永远用 `csp brief` 现算，不直接读旧 `cards.jsonl`。

## 不要做

- 不要为了让检查通过而删掉真实副作用、把 `@effect` 写成 `none`；
- 不要手写或手改 `@calls`——工具会按事实重写；
- 不要在测试代码里套用生产标准（`tests/` 默认跳过）；
- 不要把卡片当源码读——编辑前仍要读精确原文。
