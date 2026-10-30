# code-standard-python：事实由工具写，意图由人写

> 核心判断：
> **能算出来的（调用、参数、返回、raise 点）由工具生成并写回代码；
> 算不出来的（副作用、纯度、前置条件、废弃）由人写，且必须可对账。**

---

## 1. 两条原则

1. **关系类信息只有一个来源：AST。** `@calls` 由 `csp sync --write` 生成并写回
   docstring，人不手写、不手改；`called_by` 是全局反向关系，只放在卡片里。
2. **意图类标签必须能被规则检查或明确标注不可判定。** 写得漂亮但不真实，
   比不写更危险。

---

## 2. 代码形式

### 2.1 函数 / 方法

```python
def pay_claim(clid: str) -> Claim:
    """赔付：回款、结案、包裹转理赔关闭、网点责任 +1。

    @effect: db.write(claims, parcels, customers, stations, waybills), log
    @raises: ValueError 当理赔单未定损或状态非法
    @pre: clid 存在且状态为 CLS_ASSESSING
    @mutates: -
    @calls: store.get_claim, store.save_claim, _refresh_station_status, _emit
    """
```

| 项 | 要求 |
|---|---|
| 摘要 | docstring 首行一句话，不以 `@` 开头 |
| type hints | 每个参数（含 `*args`/`**kwargs`/仅关键字）与返回值 |
| 副作用 | `@pure` 或 `@effect: <种类>(<目标>)`，二选一 |
| `@raises` | 固定槽位：按异常类型逐行声明；无直接 raise 写 `-` |
| `@pre` | 固定槽位：有前置条件写条件；没有写 `-`；未确认可留空 |
| `@mutates` | 固定槽位：改了哪些参数/`self` 字段；没有写 `-`；未确认可留空 |
| `@calls` | **工具生成**，不要手写；没有内部调用时是 `@calls: -` |

**固定槽位**：每个函数（含嵌套函数）必须有 `@effect`（或 `@pure`）、`@calls`、`@raises`、
`@pre`、`@mutates` 五个槽位；缺失的 `@raises`/`@pre`/`@mutates`/`@calls` 由
`csp sync --write` 自动补齐（`@raises` 会填直接 raise 的类型，其余留空）。
空值表示“未声明”，`-` 表示“明确没有”，二者不可混用。

### 2.2 模块

```python
"""订单服务：下单、状态流转与理赔。

@stable
@calls: -
"""
```

必须有摘要；`@stable` / `@experimental` 二选一（`--strict` 下必填）。

### 2.3 类

必须有摘要。类上只允许 `@invariant`，不写函数级标签；方法各自标注。

### 2.4 嵌套函数

与普通函数同标准：摘要、type hints、`@pure`/`@effect`、五个固定槽位、`@calls`。
`csp sync --write` 会为单行 docstring 自动展开并补齐槽位。

---

## 3. 标签清单

| 标签 | 级别 | 值 | 语义 |
|---|---|---|---|
| `@pure` | 必填其一 | — | 无副作用、可重复调用 |
| `@effect` | 必填其一 | `kind(target), ...` | 结构化副作用，种类见下 |
| `@calls` | 工具生成 | 项目内精确调用，跨文件带模块前缀；`~` 表示 lambda 内 | 本函数/模块调用了谁 |
| `@raises` | 固定槽位 | 类型 + 说明，或 `-` | 可能抛出的异常 |
| `@pre` | 固定槽位 | 条件、`-` 或留空 | 调用前必须成立的语义条件 |
| `@deprecated` | 选填 | 替代方案 | 废弃，给出替代 |
| `@thread-safe` / `@not-thread-safe` | 选填 | — | 并发安全，互斥 |
| `@mutates` | 固定槽位 | `self.x, arg`、`-` 或留空 | 被修改的目标 |
| `@env` | 选填 | 环境变量 | 运行时依赖的配置项 |
| `@pii` | 选填 | 字段 | 涉及的个人信息 |
| `@idempotent` / `@transactional` | 选填 | — | 可重试 / 自带事务边界 |
| `@invariant` | 类或函数 | 不变量 | 维持的约束 |
| `@stable` / `@experimental` | 模块级 | — | API 稳定性，互斥 |

**效果种类**（`@effect` 只能取这些）：
`none`、`db.read`、`db.write`、`file.read`、`file.write`、`net`、`log`、
`env`、`clock`、`random`、`process`、`state`、`ui`。
`@effect: none` 等价于 `@pure`；目标可选，建议写具体对象（表名/路径/变量名）。

**已废弃**：`@side-effect`（自由文本，C20 提示迁移）、`@called-by`、`@entry`、
`@depends`（关系在卡片里，C15 提示）。

---

## 4. 内联与卡片的分工

| 信息 | 位置 | 原因 |
|---|---|---|
| 签名、参数、返回、raise 点 | 代码（AST 可直接读） | 与代码同源 |
| `@calls` | 代码（docstring，工具写） | 局部关系，读者打开函数就能看到 |
| `called_by` | 卡片 `cards.jsonl` | 全局反向关系，写进每个函数会让每次改动波及多处 |
| 置信度、调用行号、目标位置 | 卡片 | 细节多，代码里放不下 |
| 文件/符号哈希 | 卡片 | 判断卡片是否过期 |

`@calls` 的格式：

- `@calls: -`：没有项目内调用；
- `@calls: a, Class.m`：精确解析到定义；
- `@calls: ~helper`：只在 lambda 内调用（`~` 表示延迟/条件执行）；
- 模块 docstring 的 `@calls`：import 时执行的项目函数调用（C25 提示风险）；
- builtin（`len`/`print`）不写入。

---

## 5. 规则表

| 规则 | 级别 | 内容 |
|---|---|---|
| C0 | error | 源码无法读取/解析 |
| C1 | error | 模块缺摘要 |
| C2 | error | `@stable`/`@experimental` 冲突；strict 下缺失 |
| C3 | warning | 类缺摘要 |
| C4 | error | 函数缺摘要 |
| C5 | error | type hints 不全 |
| C6 | error | 缺少 `@pure`/`@effect`，或两者同存 |
| C7 | error | 声明无副作用但函数体有副作用源 |
| C8 | error | 无副作用函数经调用链（≤3 跳）到达有副作用的函数 |
| C9 | error | 有 raise 但 `@raises` 未声明 |
| C10 | warning | 无调用却声明了未 raise 的异常 |
| C11 | error | 可变默认参数 |
| C12 | error | 必须有值的标签为空 |
| C13 | error | 线程安全标签冲突 |
| C14 | error | 未知/越界标签，或 `@effect` 非法 |
| C15 | warning | 仍有 `@called-by`/`@entry`/`@depends` |
| C16 | error | `@deprecated` 仍被调用 |
| C20 | warning | 旧 `@side-effect` 未改成 `@effect` |
| C21 | error | `@calls` 与工具生成的事实不一致（含手改） |
| C22 | warning | 缺少 `@calls`（函数或模块，跑 `sync --write`） |
| C23 | warning | 缺少固定槽位 `@raises`/`@pre`/`@mutates` |
| C24 | warning | 写了 global/nonlocal 但 `@effect` 未声明 `state` |
| C25 | warning | 模块级代码在 import 时调用了项目函数 |
| C26 | warning | 参数被改写但 `@mutates` 未声明 |
| C27 | warning | 检测到效果（open/print/env/clock/random/process/db/net）但 `@effect` 未声明 |
| C28 | warning | 下游效果未在本函数 `@effect` 声明 |
| C29 | warning | 使用了环境变量但 `@env` 未声明 |
| T0 | error | 类型检查器无法运行（未安装/崩溃） |
| T1 | error | mypy 报告的类型错误 |

`--strict` 把 warning 升级为 error，并额外要求模块稳定性标签。

---

## 6. 命令

```bash
csp init                       # 写 .code-standard.json + AGENTS.md
csp brief --file x.py          # 生成上下文：规则 + 约定 + 现有符号/调用
csp prompt / csp rules         # 打印生成提示 / 规则表
csp sync  --root . --write     # 生成卡片 + 回填 @calls
csp check --root .             # 对账（error 退出码 1）
csp check --root . --strict    # CI 用
csp check --root . --changed   # 只看相对 HEAD 的改动
csp check --root . --staged    # 只看暂存文件（pre-commit）
csp stats --root .             # 规模、解析率、置信度分布
```

`--root` 缺省取最近的 `.git` 根。`tests/`、`.venv/`、`node_modules/` 等默认跳过。

生成流程：

```bash
csp init                                  # 项目一次
csp brief --file service.py               # 规则 + 现有事实
# 写/改代码（只写意图标签，不写 @calls）
csp sync --root . --write                 # 回填 @calls
csp check --root . --paths service.py     # 0 error 才算完成
```

---

## 7. 覆盖范围与边界

**已进入卡片/规则的构造**：

- 普通函数、方法、嵌套函数、属性 getter/setter（各自成卡，setter 带
  `.setter` 后缀避免同名冲突）；
- 会逃出函数的 `raise`（被本函数 `except` 捕获的不算，C9 不再误报）；
- 异常沿调用链的传播（`facts.propagated_raises`）；
- lambda 内的调用（`@calls: ~name`，卡片同时标 `in_lambda`）；
- 函数被当值使用（回调、注册、赋值、放进容器）：记 `references` /
  `referenced_by`，带 `context`（argument/alias/container/return），不写进 `@calls`
  ——它不是调用，只是“可能稍后被调用”；
- 继承与 `super()`：类卡记录 `bases`，`self.m()` / `cls.m()` / `super().m()`
  会沿基类查找；
- 包级重导出：`from pkg import X` 会顺着 `pkg/__init__.py` 的 `from ... import`
  或 `from ... import *` 再找一层；
- 类字段：dataclass / 普通类属性记录名字、类型、默认值（`facts.fields`）；
- 参数改写：`arg.x = ...`、`arg[i] = ...`、`del arg.x` 记入
  `facts.mutated_params`，与 `@mutates` 不符时 C26 提示；
- `global` / `nonlocal` 的读写（写进 `facts.globals`；写外部状态未标
  `@effect: state(...)` 时 C24 提示）；
- 效果静态检测：`open`/`print`/`os.environ`/`datetime.now`/`random`/
  `subprocess`/SQL 执行等可识别 API 与 `@effect` 不符时 C27 提示；
- 模块级/类体在定义时执行的调用（写进 `definition_calls`；模块级调用项目函数
  会触发 C25）；
- 装饰器与默认值里的调用（写进函数的 `definition_calls`）；
- 生成器（`is_generator`）、`@overload`（标记后跳过逐函数对账）、
  `assert`（`facts.asserts`）、`__all__`（`facts.exports`）；
- async 函数与 `await` 调用。

**明确取舍（不写进函数标签）**：

- 模块常量：只作为普通代码存在，不建卡；
- 闭包变量的“读取”：只有 `nonlocal` 声明会被记录，纯读取不追踪；
- 命名空间包、跨模块装饰器内部行为：仍可能退化为 `name_only`。

**仍不承诺**：

- `@pre`、`@thread-safe`、`@pii`、`@env` 只查格式，语义靠评审；
- `@mutates` 只核对“显式属性/下标改写”，通过方法调用（`arg.append`）的改写查不到；
- **类型正确性**：默认只查“有没有注解”；需要 `csp check --types`（调用 mypy）才查
  “注解是否相容”；
- `@effect` 只能检测已登记的 API；封装函数（`save()` 内部写库）与 ORM 细节仍需人判断；
- 测试代码默认不检查；只支持 Python；
- 卡片是派生产物，不入库。

---

## 8. 从 v1 迁移

1. `csp sync --root . --write`：用事实重写所有 `@calls`（假声明、漏写一次修好）；
2. 按 C20 提示把 `@side-effect: <描述>` 改成 `@effect: <种类>(<目标>)`；
3. 删除 `@called-by` / `@entry` / `@depends`（C15）；
4. `csp check --strict` 接 CI；钩子用 `csp check --staged --strict`。
