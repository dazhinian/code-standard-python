# 常见失败与修法

以下是按 v2 规范生成代码时最常触发的 10 类问题，每类给出错误写法与修法。

## 1. 模块没有 docstring（C1）

```python
# 错
import json
```

```python
"""报价服务：订单 → 应付金额。

@stable
"""
import json
```

## 2. 函数没有摘要或首行就是标签（C4）

```python
def subtotal(order):
    """
    @pure
    """
```

摘要必须是首行的自然语言；标签放空行之后。

## 3. type hints 不全（C5）

漏的往往是仅关键字参数、`*args`、`**kwargs` 和返回值；`self`/`cls` 除外。

## 4. 声明无副作用却做 IO（C7/C8）

```python
def save(path, quote):
    """写文件。

    @pure
    """
    with open(path, "w") as fh: ...
```

`open`/`print`/`sys.exit`/`global`/`yield` 都会让 `@pure` 失真。
修法：改标 `@effect: file.write(path)`（或对应种类）。

## 5. 漏了 `@raises`（C9）

函数体直接 `raise ValueError(...)` 就必须有一行
`@raises: ValueError <说明>`。多个类型分行写。

## 6. 声明了下游才可能抛的异常（C10）

`@raises: OSError` 而函数体只调用了 `open()`：如果函数体里有调用，
C10 不报（视为下游传播）；只有“没有任何调用却凭空声明”才提示。

## 7. 可变默认参数（C11）

```python
def quote(order, cache=set()): ...
def shipping(order, extra=[]): ...
```

改成 `None` 哨兵，在函数体内新建。

## 8. 标签用错作用域（C14）

`@stable`/`@experimental` 只在模块；`@pure`/`@effect` 只在函数/方法；
类上只允许 `@invariant`。放错层级会被 C14 抓住。

## 9. 效果写错（C14/C20）

```python
@effect: 改数据库          # 错：不是注册表里的种类
@side-effect: 改数据库     # 旧格式，C20 提示迁移
```

```python
@effect: db.write(claims)  # 对
```

种类只能是：`none/db.read/db.write/file.read/file.write/net/log/env/clock/
random/process/state/ui`；`none` 不能和其它效果并列。

## 10. `@calls` 手写或手改（C21/C22）

```python
@calls: store.get_customer, notify
```

`@calls` 由 `csp sync --write` 生成；手写会漏、会错，手改会被 C21 抓住。
改完代码后跑一次 `sync --write`，再跑 `check`。没有调用时工具会写 `@calls: -`。

---

## 一个典型修复循环

1. 生成代码后 `csp sync --root . --write`（回填 `@calls`）；
2. `csp check --root . --paths pricing.py`；
3. 按输出逐条修：先 C1/C4/C5（结构），再 C9/C11（行为），最后 C6/C7（效果）；
4. 重跑直到“通过：声明与事实一致”；
5. 检查卡片里 `calls` 的 `confidence`：`name_only` 较多说明解析不到，需人工核实。
