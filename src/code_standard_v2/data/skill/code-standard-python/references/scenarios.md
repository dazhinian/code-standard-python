# 边界场景处置手册

SKILL 只能约束“怎么做”，不能改变工具能力。下面每一条都区分：
**skill 能做的** / **仍需工具支持**。

## 1. 遗留仓库（没有标签、没有 @calls）

- 迁移模式：`csp check --root .` 先看规模，**不要直接开 `--strict`**；
- 按文件推进：`csp sync --root . --write` 只修 `@calls`，再逐个文件处理 C20（旧
  `@side-effect`）；
- 一次只改一个模块，改完 `csp check --paths <文件>`；
- 不要为了清零而删真实副作用或把 `@effect` 写成 `none`。
- **仍需工具**：baseline/豁免机制，否则大仓库一次性全红。

## 2. 动态/框架代码（FastAPI、Django ORM、pytest fixture、DI）

- 框架入口（路由、hook、fixture）手工写清 `@effect`/`@pre`，它们不会从框架里推导；
- 看到 `name_only` 调用，用 `csp brief --symbol <名字>` 或 grep 确认目标；
- 项目约定（如“所有 DB 访问走 repository”）写进 `AGENTS.md`，brief 会带上；
- **仍需工具**：`@dynamic` 标记与“不可解析调用”提示。

## 3. 新代码调用已有函数（防拼写/漏解析）

- 写完后，对每个项目内调用执行 `csp brief --symbol <名字>`；查不到卡片就是不存在；
- `check` 通过不代表调用解析成功：`name_only` 不报错，必须人工确认；
- 不确定参数语义时读目标函数原文或问人，不要按名字猜。
- **仍需工具**：对“看起来像项目内名字”的未解析调用给出警告。

## 4. 多人 / 多 agent 同时改

- `csp sync --write` **串行执行**；不要两个 agent 同时跑；
- 合并冲突时不要手改 `@calls`，重新跑一次 `sync --write`；
- 提交前各自 `csp check --staged --strict`。
- **仍需工具**：文件锁或只写“本次改动函数”的增量模式。

## 5. 大仓库

- 日常用 `csp check --changed` / `--staged`，别全量；
- 生成上下文用 `brief --file` / `--symbol`，不要整仓 brief；
- 全量 `--strict` 只放 CI。
- **仍需工具**：增量索引与缓存；否则每条命令都全量分析。

## 6. 只读 / CI 环境

- `sync --write` 只在开发侧跑，把 `@calls` 随代码提交；
- CI 只跑 `csp check --root . --strict`；
- 不要指望 CI 自动补标签。
- **skill 可完全覆盖**（靠流程约束）。

## 7. 生成代码 / vendor

- 先把目录写进 `.code-standard.json` 的 `skip_dirs`，再跑 `sync`；
- 不要对生成文件跑 `--write`，否则下次生成即冲突；
- 生成代码不写标签，也不纳入 `check`。
- **skill + 配置可覆盖**。

## 8. 测试代码

- 默认 `tests/` 不检查；不要声称“测试也符合标准”；
- 若团队要求测试也标准，显式传路径并在 CI 单独跑；
- 新写的测试至少保证能跑，`check` 不负责正确性。
- **skill 可覆盖（明确边界）**。

## 9. 多语言 / monorepo

- `--root` 指向 Python 项目根，不要指向 monorepo 顶层；
- 非 Python 部分用各自工具，不要假装卡片覆盖；
- 跨语言调用（Python 调 TS/API）只能人工维护契约。
- **skill 可覆盖（范围约束）**。

## 10. 卡片读取纪律

- 永远用 `csp brief` 现算，不直接读旧 `cards.jsonl`；
- 需要批量查询时先 `csp sync`，再读卡片；
- 卡片是派生产物，不提交、不作为事实源。
- **skill 可完全覆盖**。
