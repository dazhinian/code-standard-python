"""v2 抽取/解析/对账的回归测试。

@stable
"""
from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest

from code_standard_v2.check import check_project
from code_standard_v2.cli import analyze
from code_standard_v2.cli import main as cli_main
from code_standard_v2.cli import read_prompt
from code_standard_v2.extract import extract_file
from code_standard_v2.tags import parse_tags


def rules(findings: list) -> dict[str, int]:
    """按规则号汇总 findings 数量。

    @pure
    """
    out: dict[str, int] = {}
    for item in findings:
        out[item.rule] = out.get(item.rule, 0) + 1
    return out


class ProjectCase(unittest.TestCase):
    """在临时目录里建多文件项目并跑完整分析。

    @pure
    """

    def project(self, files: dict[str, str], strict: bool = False):
        """写文件、抽取、对账，返回 (cards, errors_only, findings)。

        @effect: file.write
        """
        root = self.root_with(files)
        cards, _stats, source_errors = analyze(root)
        findings = check_project(cards, strict=strict) + source_errors
        return cards, [item for item in findings if item.severity == "error"], findings

    def root_with(self, files: dict[str, str]) -> str:
        """建一个临时项目目录并写入文件。

        @effect: file.write
        """
        root = tempfile.mkdtemp(prefix="cs2test-")
        for name, text in files.items():
            path = os.path.join(root, name)
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        return root

    def card(self, cards, name: str):
        """按名字取卡片。

        @pure
        """
        for item in cards:
            if item.name == name and item.kind in ("function", "method"):
                return item
        raise AssertionError(f"card not found: {name}")


class ExtractTests(ProjectCase):
    """参数、标签、哈希等事实抽取。

    @pure
    """

    def test_params_and_signature(self) -> None:
        """posonly/kwonly/vararg/kwarg 与默认值都能落到卡片。"""
        cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""


def f(a, /, b: int = 1, *args: str, c: str = "x", **kw: int) -> None:
    """F.

    @pure
    """
    return None
'''})
        card = self.card(cards, "f")
        kinds = [(param.name, param.kind) for param in card.params]
        self.assertEqual(kinds, [("a", "posonly"), ("b", "pos"),
                                 ("args", "vararg"), ("c", "kwonly"),
                                 ("kw", "kwarg")])
        self.assertEqual(card.params[1].default, "1")
        self.assertEqual(card.params[3].default, "'x'")
        self.assertEqual(card.returns, "None")
        self.assertIn("/", card.signature)
        self.assertIn("*", card.signature)
        self.assertEqual([item.rule for item in errors], ["C5"])

    def test_tags_and_summary(self) -> None:
        """标签值、行号与摘要被正确解析。"""
        doc = "  摘要。\n\n  @effect: db.write(t)\n  @raises: ValueError 说明\n"
        tags, lines = parse_tags(doc, 10)
        self.assertEqual(tags["effect"], ["db.write(t)"])
        self.assertEqual(tags["raises"], ["ValueError 说明"])
        self.assertEqual(lines["effect"], 12)

    def test_bom_file_is_readable(self) -> None:
        """含 BOM 的 UTF-8 文件不会解析失败。"""
        root = tempfile.mkdtemp(prefix="cs2bom-")
        path = os.path.join(root, "m.py")
        with open(path, "wb") as handle:
            handle.write("\ufeff".encode("utf-8")
                         + b'"""M.\n\n@stable\n"""\n')
        facts = extract_file(path, "m.py")
        self.assertEqual(len(facts.cards), 1)


class CheckTests(ProjectCase):
    """对账规则的命中与豁免。

    @pure
    """

    def test_missing_raises(self) -> None:
        """calls @raises 缺失 → C9；多余声明 → C10。"""
        cards, errors, findings = self.project({"m.py": '''"""M.

@stable
"""


def f() -> None:
    """F.

    @pure
    """
    raise ValueError("x")
'''})
        self.assertEqual(rules(errors).get("C9"), 1)
        self.assertEqual(rules(findings).get("C10"), None)

    def test_extra_raises_is_warning(self) -> None:
        """声明了未直接 raise 的类型 → C10 warning。"""
        _cards, errors, findings = self.project({"m.py": '''"""M.

@stable
"""


def f() -> None:
    """F.

    @pure
    @raises: KeyError 下游
    """
    return None
'''})
        self.assertEqual(errors, [])
        self.assertEqual(rules(findings).get("C10"), 1)

    def test_mutable_defaults(self) -> None:
        """list 字面量与 set() 调用都算可变默认参数。"""
        _cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""


def f(x: list = []) -> None:
    """F.

    @pure
    """
    return None


def g(y: set = set()) -> None:
    """G.

    @pure
    """
    return None
'''})
        self.assertEqual(rules(errors).get("C11"), 2)

    def test_pure_violations(self) -> None:
        """print/sys.exit 冲突；self.exit() 不算进程退出。"""
        _cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""

import sys


def f() -> None:
    """F.

    @pure
    """
    print("x")


def g(self) -> None:
    """G.

    @pure
    """
    self.exit()


def h() -> None:
    """H.

    @pure
    """
    sys.exit(1)
'''})
        messages = [item.symbol for item in errors if item.rule == "C7"]
        self.assertIn("f", messages)
        self.assertIn("h", messages)
        self.assertNotIn("g", messages)

    def test_pure_calls_impure(self) -> None:
        """@pure 调用 @side-effect → C8。"""
        _cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""


def save() -> None:
    """Save.

    @effect: db.write(t)
    """
    pass


def query() -> None:
    """Query.

    @pure
    """
    save()
'''})
        self.assertEqual(rules(errors).get("C8"), 1)

    def test_deprecated_called(self) -> None:
        """废弃函数仍被调用 → C16。"""
        _cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""


def old() -> None:
    """Old.

    @pure
    @deprecated: new
    """
    pass


def new() -> None:
    """New.

    @pure
    """
    old()
'''})
        self.assertEqual(rules(errors).get("C16"), 1)

    def test_unknown_tag(self) -> None:
        """拼错的标签 → C14。"""
        _cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""


def f() -> None:
    """F.

    @puer
    """
    pass
'''})
        self.assertEqual(rules(errors).get("C14"), 1)

    def test_nested_function_requires_effect_and_slots(self) -> None:
        """嵌套函数与普通函数同标准：必须有纯度与固定槽位。"""
        _cards, errors, findings = self.project({"m.py": '''"""M.

@stable
"""


def outer() -> None:
    """Outer.

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: inner
    """

    def inner(x: int) -> int:
        """Inner.
        """
        return x

    inner(1)
'''})
        self.assertEqual(rules(errors).get("C6"), 1)
        self.assertEqual(rules(findings).get("C23"), 1)

    def test_module_and_class_docstring(self) -> None:
        """模块与类缺摘要时报 C1/C3。"""
        _cards, errors, findings = self.project({"m.py": '''class A:
    pass
'''})
        self.assertEqual(rules(errors).get("C1"), 1)
        self.assertEqual(rules(findings).get("C3"), 1)


class ResolveTests(ProjectCase):
    """调用边解析与置信度。

    @pure
    """

    def test_self_import_and_unknown(self) -> None:
        """self 方法/import/未知名字分别得到 exact 与 name_only。"""
        cards, _errors, _ = self.project({
            "store.py": '''"""Store.

@stable
"""


def save(x: int) -> None:
    """Save.

    @pure
    """
    return None
''',
            "service.py": '''"""Service.

@stable
"""

import store


class S:
    """S."""

    def run(self) -> None:
        """Run.

        @pure
        """
        self.helper()
        store.save(1)
        mystery()

    def helper(self) -> None:
        """Helper.

        @pure
        """
        pass
''',
        })
        run = self.card(cards, "run")
        by_name = {item["name"]: item["confidence"] for item in run.resolved_calls}
        self.assertEqual(by_name["helper"], "exact")
        self.assertEqual(by_name["save"], "exact")
        self.assertEqual(by_name["mystery"], "name_only")
        helper = self.card(cards, "helper")
        self.assertEqual(len(helper.called_by), 1)


class ScopeTests(ProjectCase):
    """作用域相关回归：方法必须有纯度声明，类上不能挂函数标签。

    @pure
    """

    def test_method_requires_purity(self) -> None:
        """类内方法不会被误判为嵌套函数而豁免 C6。"""
        _cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""


class S:
    """S."""

    def run(self) -> None:
        """Run."""
        return None
'''})
        self.assertEqual(rules(errors).get("C6"), 1)

    def test_class_tag_scope(self) -> None:
        """类上写 @pure 属于越界标签 → C14。"""
        _cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""


class S:
    """S.

    @pure
    """
    pass
'''})
        self.assertEqual(rules(errors).get("C14"), 1)

    def test_mutable_default_through_list_call(self) -> None:
        """list() 默认值同样算可变默认参数。"""
        _cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""


def f(x: list = list()) -> None:
    """F.

    @pure
    """
    return None
'''})
        self.assertEqual(rules(errors).get("C11"), 1)


class PurityChainTests(ProjectCase):
    """传递纯度：pure → pure → side-effect 也要被抓住。

    @pure
    """

    def test_two_hop_purity(self) -> None:
        """两跳之后到达 @side-effect → C8。"""
        _cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""


def save() -> None:
    """Save.

    @effect: db.write(t)
    """
    pass


def helper() -> None:
    """Helper.

    @pure
    """
    save()


def entry() -> None:
    """Entry.

    @pure
    """
    helper()
'''})
        self.assertEqual(rules(errors).get("C8"), 2)


class RegistryTests(unittest.TestCase):
    """规则注册表与生成提示的可用性。

    @pure
    """

    def test_rules_cover_code(self) -> None:
        """源码里出现的规则号都必须登记在 RULES 中。"""
        import re

        from code_standard_v2.rules import RULES

        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        used: set[str] = set()
        for name in os.listdir(os.path.join(here, "src", "code_standard_v2")):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(here, "src", "code_standard_v2", name),
                      encoding="utf-8") as handle:
                used.update(re.findall(r'"(C\d+)"', handle.read()))
        self.assertTrue(used)
        self.assertEqual(used - set(RULES), set())

    def test_prompt_is_packaged(self) -> None:
        """生成提示随包发布，且包含关键约束。"""
        from code_standard_v2.cli import read_prompt

        text = read_prompt()
        self.assertIn("csp sync", text)
        self.assertIn("不写", text)


class GitTests(ProjectCase):
    """git 改动过滤（--changed/--staged 的数据源）。

    @effect: file.write, process
    """

    def _git_repo(self) -> str:
        """建一个提交过一次的临时仓库；git 不可用时跳过。

        @effect: file.write, process
        """
        import shutil
        import subprocess

        if shutil.which("git") is None:
            self.skipTest("git 不可用")
        root = tempfile.mkdtemp(prefix="cs2git-")
        try:
            subprocess.run(["git", "-C", root, "init", "-q"], check=True,
                           capture_output=True)
            subprocess.run(["git", "-C", root, "config", "user.email", "t@t.t"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", root, "config", "user.name", "t"],
                           check=True, capture_output=True)
            with open(os.path.join(root, "a.py"), "w", encoding="utf-8") as handle:
                handle.write('"""A.\n\n@stable\n"""\n')
            subprocess.run(["git", "-C", root, "add", "a.py"], check=True,
                           capture_output=True)
            subprocess.run(["git", "-C", root, "commit", "-qm", "init"], check=True,
                           capture_output=True)
        except (OSError, subprocess.CalledProcessError, PermissionError):
            self.skipTest("当前环境无法创建 git 仓库")
        return root

    def test_changed_and_staged(self) -> None:
        """改动/未跟踪文件被识别，未改动文件不出现。"""
        from code_standard_v2.cli import changed_files

        root = self._git_repo()
        with open(os.path.join(root, "a.py"), "a", encoding="utf-8") as handle:
            handle.write("\nX = 1\n")
        with open(os.path.join(root, "b.py"), "w", encoding="utf-8") as handle:
            handle.write('"""B.\n\n@stable\n"""\n')
        changed = changed_files(root)
        self.assertEqual(changed, {"a.py", "b.py"})
        staged = changed_files(root, staged=True)
        self.assertEqual(staged, set())


class ConfigTests(ProjectCase):
    """项目策略：初始化、跳过目录、strict 默认值。

    @effect: file.write
    """

    def test_init_writes_config_and_agents(self) -> None:
        """init 写配置与 AGENTS.md，重复执行不重复追加。"""
        root = self.root_with({})
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            first = cli_main(["init", "--root", root])
            second = cli_main(["init", "--root", root])
            forced = cli_main(["init", "--root", root, "--force"])
        self.assertEqual((first, second, forced), (0, 2, 0))
        self.assertTrue(os.path.exists(os.path.join(root, ".code-standard.json")))
        with open(os.path.join(root, "AGENTS.md"), encoding="utf-8") as handle:
            agents = handle.read()
        self.assertEqual(agents.count("code-standard:v2"), 1)

    def test_skip_dirs_from_config(self) -> None:
        """配置里的 skip_dirs 会被 analyze 采用。"""
        from code_standard_v2.config import Config, save

        root = self.root_with({
            "vendor/bad.py": "def f(a):\n    return a\n",
            "ok.py": '"""Ok.\n\n@stable\n"""\n',
        })
        save(root, Config(skip_dirs=["vendor"]))
        cards, _stats, _errors = analyze(root)
        self.assertNotIn("vendor/bad.py", {card.file for card in cards})

    def test_config_strict_becomes_error(self) -> None:
        """配置 strict=true 时 warning 升级为 error。"""
        from code_standard_v2.config import Config, save

        root = self.root_with({
            "m.py": '"""M.\n\n@stable\n"""\n\n\nclass A:\n    pass\n',
        })
        save(root, Config(strict=True))
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli_main(["check", "--root", root])
        self.assertEqual(code, 1)


class BriefTests(ProjectCase):
    """生成上下文组装。

    @effect: file.write
    """

    def test_brief_includes_existing_card(self) -> None:
        """brief --file 输出目标符号签名、调用与验收命令。"""
        root = self.root_with({
            "m.py": ('"""M.\n\n@stable\n"""\n\n\n'
                     'def f(a: int) -> int:\n'
                     '    """F.\n\n    @pure\n    """\n'
                     '    return a\n\n\n'
                     'def g() -> int:\n'
                     '    """G.\n\n    @pure\n    """\n'
                     '    return f(1)\n'),
        })
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            code = cli_main(["brief", "--root", root, "--file", "m.py",
                             "--no-prompt"])
        text = sink.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("f(a: int) -> int", text)
        self.assertIn("→ f", text)
        self.assertIn("csp check", text)

    def test_edit_prompt_has_modification_rules(self) -> None:
        """通用模板里包含“修改已有代码”的纪律。"""
        text = read_prompt()
        self.assertIn("修改已有代码时", text)
        self.assertIn("保留未变的标签", text)
        self.assertIn("最小 diff", text)


class InlineCallsTests(ProjectCase):
    """内联 @calls：工具写入、幂等、篡改可发现。

    @effect: file.write
    """

    BASE = '''"""M.

@stable
"""


def g() -> None:
    """G.

    @pure
    @calls: -
    """
    return None


def f() -> None:
    """F.

    @pure
    @calls: -
    """
    g()
'''

    def test_sync_write_fills_calls(self) -> None:
        """sync --write 把真实调用写入 f，随后 check 通过。"""
        root = self.root_with({"m.py": self.BASE})
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli_main(["sync", "--root", root, "--write", "--quiet"])
        self.assertEqual(code, 0)
        with open(os.path.join(root, "m.py"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("    @calls: g\n", text)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            checked = cli_main(["check", "--root", root])
        self.assertEqual(checked, 0)

    def test_sync_write_is_idempotent(self) -> None:
        """连续两次 sync --write 不再改动源码。"""
        root = self.root_with({"m.py": self.BASE})
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            cli_main(["sync", "--root", root, "--write", "--quiet"])
        with open(os.path.join(root, "m.py"), encoding="utf-8") as handle:
            first = handle.read()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            cli_main(["sync", "--root", root, "--write", "--quiet"])
        with open(os.path.join(root, "m.py"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), first)

    def test_tampered_calls_is_error(self) -> None:
        """手改 @calls → C21 error。"""
        source = self.BASE.replace(
            '    @calls: -\n    """\n    g()',
            '    @calls: nope\n    """\n    g()')
        root = self.root_with({"m.py": source})
        cards, _stats, _errors = analyze(root)
        found = check_project(cards)
        self.assertEqual(rules(found).get("C21"), 1)

    def test_missing_calls_is_warning(self) -> None:
        """没有 @calls → C22 warning，可被 sync --write 修好。"""
        source = self.BASE.replace("    @calls: -\n", "")
        root = self.root_with({"m.py": source})
        cards, _stats, _errors = analyze(root)
        found = check_project(cards)
        function_missing = [item for item in found
                            if item.rule == "C22" and item.symbol != "<module>"]
        self.assertEqual(len(function_missing), 2)


    def test_property_setter_and_overload_do_not_duplicate(self) -> None:
        """属性 getter/setter 与 @overload 同名时，每个函数只写一次槽位。"""
        root = self.root_with({"m.py": '''"""M.

@stable
@calls: -
"""
from typing import overload


class Box:
    """B."""

    @property
    def x(self) -> int:
        """X.

        @pure
        """
        return 0

    @x.setter
    def x(self, value: int) -> None:
        """Set.

        @effect: state(self._x)
        """
        self._x = value


@overload
def parse(value: int) -> int:
    """P int."""


@overload
def parse(value: str) -> int:
    """P str."""


def parse(value: int | str) -> int:
    """P.

    @pure
    """
    return int(value)
'''})
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            cli_main(["sync", "--root", root, "--write", "--quiet"])
        with open(os.path.join(root, "m.py"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(text.count("@calls:"), 4)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli_main(["check", "--root", root])
        self.assertEqual(code, 0)


class EffectTests(ProjectCase):
    """结构化效果声明。"""

    def test_unknown_effect_kind(self) -> None:
        """未知效果种类 → C14 error。"""
        _cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""


def f() -> None:
    """F.

    @effect: teleport
    """
    pass
'''})
        self.assertEqual(rules(errors).get("C14"), 1)

    def test_effect_none_equals_pure(self) -> None:
        """@effect: none 与 @pure 等价，且仍受 C7 约束。"""
        _cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""


def f() -> None:
    """F.

    @effect: none
    """
    print("x")
'''})
        self.assertEqual(rules(errors).get("C7"), 1)


class CoverageTests(ProjectCase):
    """此前未覆盖的构造：被捕获异常、lambda、global、模块级调用、overload。"""

    def test_caught_raise_is_not_declared(self) -> None:
        """被本函数 except 捕获的 raise 不算对外异常。"""
        cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""


def f() -> None:
    """F.

    @effect: none
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    try:
        raise ValueError("x")
    except ValueError:
        return None
'''})
        self.assertEqual(errors, [])
        self.assertEqual(self.card(cards, "f").raises, [])

    def test_escaping_raise_still_requires_declaration(self) -> None:
        """没有被捕获的 raise 仍然要写 @raises。"""
        _cards, errors, _ = self.project({"m.py": '''"""M.

@stable
"""


def f() -> None:
    """F.

    @effect: none
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    raise ValueError("x")
'''})
        self.assertEqual(rules(errors).get("C9"), 1)

    def test_lambda_call_recorded_and_marked_inline(self) -> None:
        """lambda 内的调用进卡片，并在 @calls 里用 ~ 标记。"""
        cards, errors, _ = self.project({"m.py": '''"""M.

@stable

@calls: -
"""


def helper(x: int) -> int:
    """H.

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    return x


def f() -> None:
    """F.

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: ~helper
    """
    run = lambda value: helper(value)
    return None
'''})
        self.assertEqual(errors, [])
        target = self.card(cards, "f")
        self.assertTrue(any(item.get("in_lambda") for item in target.resolved_calls))

    def test_global_write_warns_without_state_effect(self) -> None:
        """写 global 却没有 @effect: state → C24。"""
        source = '''"""M.

@stable
"""

counter = 0


def bump() -> None:
    """B.

    @effect: log
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    global counter
    counter += 1
'''
        _cards, errors, findings = self.project({"m.py": source})
        self.assertEqual(errors, [])
        self.assertEqual(rules(findings).get("C24"), 1)
        with_state = source.replace("@effect: log", "@effect: state(counter)")
        _cards2, _errors2, findings2 = self.project({"m.py": with_state})
        self.assertNotIn("C24", rules(findings2))

    def test_module_definition_call_warns(self) -> None:
        """模块级调用项目函数 → C25。"""
        _cards, errors, findings = self.project({"m.py": '''"""M.

@stable
"""


def init() -> None:
    """I.

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    return None


init()
'''})
        self.assertEqual(errors, [])
        self.assertEqual(rules(findings).get("C25"), 1)

    def test_overload_is_marked(self) -> None:
        """@overload 声明被标记，不当作普通函数对账。"""
        cards, _stats, _errors = analyze(self.root_with({"m.py": '''"""M.

@stable
"""

from typing import overload


@overload
def f(x: int) -> int:
    """F int."""


@overload
def f(x: str) -> str:
    """F str."""


def f(x: int | str) -> int | str:
    """F.

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    return x
'''}))
        overloads = [card for card in cards
                     if card.name == "f" and card.is_overload]
        self.assertEqual(len(overloads), 2)


class PythonFeatureTests(ProjectCase):
    """Python 特性：继承、super、类字段、参数改写、异常传播、assert、__all__。"""

    def test_inheritance_and_super_resolve(self) -> None:
        """继承方法与 super().m 都能解析到基类定义。"""
        cards, errors, _ = self.project({"m.py": '''"""M.

@stable
@calls: -
"""


class Base:
    """B."""

    def save(self) -> None:
        """S.

        @pure
        @raises: -
        @pre:
        @mutates:
        @calls: -
        """
        return None


class Child(Base):
    """C."""

    def run(self) -> None:
        """R.

        @pure
        @raises: -
        @pre:
        @mutates:
        @calls: Base.save
        """
        self.save()
        super().save()
'''})
        self.assertEqual(errors, [])
        run = self.card(cards, "run")
        internal = [item for item in run.resolved_calls
                    if item["confidence"] != "builtin"]
        self.assertTrue(all(item["confidence"] in ("exact", "self")
                            for item in internal))
        targets = [item.get("target", {}).get("qname")
                   for item in run.resolved_calls if item.get("target")]
        self.assertIn("Base.save", targets)

    def test_class_fields(self) -> None:
        """dataclass 字段被记录到类卡。"""
        cards, _errors, _ = self.project({"m.py": '''"""M.

@stable
@calls: -
"""
from dataclasses import dataclass


@dataclass
class Order:
    """O."""

    item_price: int
    quantity: int = 1
    note: str | None = None
'''})
        order = next(card for card in cards if card.name == "Order")
        names = [field["name"] for field in order.fields]
        self.assertEqual(names, ["item_price", "quantity", "note"])
        self.assertEqual(order.fields[0]["type"], "int")
        self.assertEqual(order.fields[1]["default"], "1")

    def test_mutated_param_warning(self) -> None:
        """改写参数属性但没有 @mutates → C26；声明后消失。"""
        source = '''"""M.

@stable
@calls: -
"""


def fill(order: dict) -> None:
    """F.

    @effect: state(order)
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    order["done"] = True
'''
        _cards, errors, findings = self.project({"m.py": source})
        self.assertEqual(errors, [])
        self.assertEqual(rules(findings).get("C26"), 1)
        fixed = source.replace("@mutates:\n", "@mutates: order\n")
        _cards2, _errors2, findings2 = self.project({"m.py": fixed})
        self.assertNotIn("C26", rules(findings2))

    def test_raises_propagation(self) -> None:
        """调用链上的异常被记录为 propagated_raises。"""
        cards, _errors, _ = self.project({"m.py": '''"""M.

@stable
@calls: -
"""


def inner() -> None:
    """I.

    @effect: none
    @raises: ValueError 非法
    @pre:
    @mutates:
    @calls: -
    """
    raise ValueError("x")


def outer() -> None:
    """O.

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: inner
    """
    inner()
'''})
        outer = self.card(cards, "outer")
        self.assertIn("ValueError", outer.propagated_raises)

    def test_asserts_and_exports(self) -> None:
        """assert 与 __all__ 作为事实记录。"""
        cards, _errors, _ = self.project({"m.py": '''"""M.

@stable
@calls: -
"""

__all__ = ["f"]


def f(x: int) -> int:
    """F.

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    assert x >= 0
    return x
'''})
        module = next(card for card in cards if card.kind == "module")
        self.assertEqual(module.exports, ["f"])
        target = self.card(cards, "f")
        self.assertEqual(len(target.asserts), 1)


class ReferenceTests(ProjectCase):
    """函数被当值使用（回调/注册/赋值）时记录 reference 边。"""

    def test_reference_edge(self) -> None:
        """register(handler) 记录引用，而不是调用。"""
        cards, errors, _ = self.project({"m.py": '''"""M.

@stable
@calls: -
"""


def handler() -> None:
    """H.

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    return None


def register(callback: object) -> None:
    """R.

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    return None


def setup() -> None:
    """S.

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: register
    """
    register(handler)
'''})
        self.assertEqual(errors, [])
        setup = self.card(cards, "setup")
        self.assertEqual(setup.resolved_references[0]["target"]["qname"], "handler")
        self.assertEqual(setup.resolved_references[0]["context"], "argument")
        handler = self.card(cards, "handler")
        self.assertEqual(handler.referenced_by[0]["qname"], "setup")

    def test_local_shadow_is_not_reference(self) -> None:
        """同名局部变量不算函数引用。"""
        cards, _errors, _ = self.project({"m.py": '''"""M.

@stable
@calls: -
"""


def handler() -> None:
    """H.

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    return None


def setup() -> None:
    """S.

    @pure
    @raises: -
    @pre:
    @mutates:
    @calls: -
    """
    handler = 1
    print(handler)
'''})
        setup = self.card(cards, "setup")
        self.assertEqual(setup.resolved_references, [])


class ReexportTests(ProjectCase):
    """包级重导出：from pkg import X 顺着 __init__.py 再找一层。"""

    def test_reexport_from_init(self) -> None:
        """__init__.py 的 from .service import helper 能被解析。"""
        cards, errors, _ = self.project({
            "pkg/__init__.py": '''"""P.

@stable
@calls: -
"""
from .service import helper
''',
            "pkg/service.py": '''"""S.

@stable
@calls: -
"""


def helper() -> None:
    """H.

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return None
''',
            "main.py": '''"""M.

@stable
@calls: -
"""
from pkg import helper


def use() -> None:
    """U.

    @pure
    @calls: pkg.service.helper
    @raises: -
    @pre:
    @mutates:
    """
    helper()
''',
        })
        self.assertEqual(errors, [])
        use = self.card(cards, "use")
        self.assertEqual(use.resolved_calls[0]["confidence"], "exact")
        self.assertEqual(use.resolved_calls[0]["target"]["module"], "pkg.service")

    def test_reexport_wildcard(self) -> None:
        """__init__.py 的 from .service import * 也能解析。"""
        cards, errors, _ = self.project({
            "pkg/__init__.py": '''"""P.

@stable
@calls: -
"""
from .service import *
''',
            "pkg/service.py": '''"""S.

@stable
@calls: -
"""


def helper() -> None:
    """H.

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return None
''',
            "main.py": '''"""M.

@stable
@calls: -
"""
from pkg import helper


def use() -> None:
    """U.

    @pure
    @calls: pkg.service.helper
    @raises: -
    @pre:
    @mutates:
    """
    helper()
''',
        })
        self.assertEqual(errors, [])
        use = self.card(cards, "use")
        self.assertEqual(use.resolved_calls[0]["confidence"], "exact")


class TypeCheckTests(ProjectCase):
    """mypy 可选接入：把“有注解”升级成“注解相容”。"""

    def test_parse_mypy_output(self) -> None:
        """mypy 文本行被解析成 T1 finding。"""
        from code_standard_v2.cli import _parse_mypy_output

        text = ('/tmp/x.py:3: error: Argument 1 has incompatible type "str"; '
                'expected "int"  [arg-type]')
        findings = _parse_mypy_output(text, "/tmp")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "T1")
        self.assertEqual(findings[0].line, 3)
        self.assertIn("arg-type", findings[0].message)

    def test_check_types_end_to_end(self) -> None:
        """传错类型时 --types 让 check 失败（mypy 未安装则跳过）。"""
        import importlib.util

        if importlib.util.find_spec("mypy") is None:
            self.skipTest("mypy 未安装")
        root = self.root_with({"m.py": '''"""M.

@stable
@calls: -
"""


def add(a: int, b: int) -> int:
    """A.

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return a + b


def use() -> int:
    """U.

    @pure
    @calls: add
    @raises: -
    @pre:
    @mutates:
    """
    return add("x", 1)
'''})
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = cli_main(["check", "--root", root, "--types"])
        self.assertEqual(code, 1)


class EffectSourceTests(ProjectCase):
    """效果静态检测（C27）与方法调用式改写（C26）。"""

    def test_open_write_requires_file_write(self) -> None:
        """open(..., 'w') 但 @effect 只写 log → C27 file.write。"""
        source = '''"""M.

@stable
@calls: -
"""


def dump() -> None:
    """D.

    @effect: log
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    with open("x.txt", "w", encoding="utf-8") as handle:
        handle.write("x")
'''
        _cards, errors, findings = self.project({"m.py": source})
        self.assertEqual(errors, [])
        self.assertEqual(rules(findings).get("C27"), 1)
        fixed = source.replace("@effect: log", "@effect: file.write")
        _cards2, _errors2, findings2 = self.project({"m.py": fixed})
        self.assertNotIn("C27", rules(findings2))

    def test_clock_breaks_pure(self) -> None:
        """@pure 里调用 datetime.now → C7（检测到 clock）。"""
        _cards, errors, _ = self.project({"m.py": '''"""M.

@stable
@calls: -
"""
from datetime import datetime


def now() -> str:
    """N.

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return datetime.now().isoformat()
'''})
        self.assertEqual(rules(errors).get("C7"), 1)

    def test_append_marks_param_mutated(self) -> None:
        """arg.append(...) 也算改写参数 → C26。"""
        source = '''"""M.

@stable
@calls: -
"""


def fill(items: list) -> None:
    """F.

    @effect: state(items)
    @calls: -
    @raises: -
    @pre:
    @mutates: -
    """
    items.append(1)
'''
        _cards, errors, findings = self.project({"m.py": source})
        self.assertEqual(errors, [])
        self.assertEqual(rules(findings).get("C26"), 1)
        fixed = source.replace("@mutates: -", "@mutates: items")
        _cards2, _errors2, findings2 = self.project({"m.py": fixed})
        self.assertNotIn("C26", rules(findings2))


class ProtocolTests(ProjectCase):
    """隐式协议调用：property setter、with、for。"""

    def test_property_setter_edge(self) -> None:
        """self.x = ... 触发 property setter 的协议边。"""
        cards, errors, _ = self.project({"m.py": '''"""M.

@stable
@calls: -
"""


class Box:
    """B."""

    def __init__(self) -> None:
        """I.

        @effect: state(self._x)
        @calls: -
        @raises: -
        @pre:
        @mutates: self
        """
        self._x = 0

    @property
    def x(self) -> int:
        """X.

        @pure
        @calls: -
        @raises: -
        @pre:
        @mutates: -
        """
        return self._x

    @x.setter
    def x(self, value: int) -> None:
        """Set x.

        @effect: state(self._x)
        @calls: -
        @raises: -
        @pre:
        @mutates: self
        """
        self._x = value

    def reset(self) -> None:
        """R.

        @effect: state(self._x)
        @calls: -
        @raises: -
        @pre:
        @mutates: self
        """
        self.x = 0
'''})
        self.assertEqual(errors, [])
        reset = self.card(cards, "reset")
        self.assertEqual(reset.protocol_calls[0]["target"]["qname"], "Box.x.setter")

    def test_with_and_for_edges(self) -> None:
        """with/for 按参数注解找到 __enter__/__exit__/__iter__。"""
        cards, errors, _ = self.project({"m.py": '''"""M.

@stable
@calls: -
"""


class Res:
    """R."""

    def __enter__(self) -> "Res":
        """E.

        @pure
        @calls: -
        @raises: -
        @pre:
        @mutates: -
        """
        return self

    def __exit__(self, *args: object) -> None:
        """X.

        @pure
        @calls: -
        @raises: -
        @pre:
        @mutates: -
        """
        return None

    def __iter__(self) -> object:
        """I.

        @pure
        @calls: -
        @raises: -
        @pre:
        @mutates: -
        """
        return iter([])


def use(res: Res) -> None:
    """U.

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates: -
    """
    with res:
        pass
    for _item in res:
        pass
'''})
        self.assertEqual(errors, [])
        use = self.card(cards, "use")
        targets = {item["target"]["qname"] for item in use.protocol_calls}
        self.assertEqual(targets, {"Res.__enter__", "Res.__exit__", "Res.__iter__"})


class DbAndEnvTests(ProjectCase):
    """SQLite 查询层、过期检测、@env 核对。"""

    def test_db_query_and_stale(self) -> None:
        """db build 后可查 callers；文件改动后 stale 报出。"""
        from code_standard_v2 import db as index_db

        root = self.root_with({"m.py": '''"""M.

@stable
@calls: -
"""


def target() -> None:
    """T.

    @pure
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return None


def caller() -> None:
    """C.

    @pure
    @calls: target
    @raises: -
    @pre:
    @mutates:
    """
    target()
'''})
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli_main(["db", "build", "--root", root]), 0)
        self.assertEqual(index_db.stale_files(root), [])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli_main(
                ["query", "callers", "target", "--root", root]), 0)
        self.assertIn("caller", buffer.getvalue())
        with open(os.path.join(root, "m.py"), "a", encoding="utf-8") as handle:
            handle.write("\n")
        self.assertIn("m.py", index_db.stale_files(root))

    def test_env_name_check(self) -> None:
        """os.environ 使用但 @env 未声明 → C29。"""
        source = '''"""M.

@stable
@calls: -
"""
import os


def url() -> str:
    """U.

    @effect: env
    @calls: -
    @raises: -
    @pre:
    @mutates:
    """
    return os.environ.get("DB_URL", "")
'''
        _cards, errors, findings = self.project({"m.py": source})
        self.assertEqual(errors, [])
        self.assertEqual(rules(findings).get("C29"), 1)
        fixed = source.replace("@mutates:", "@env: DB_URL\n    @mutates:")
        _cards2, _errors2, findings2 = self.project({"m.py": fixed})
        self.assertNotIn("C29", rules(findings2))


if __name__ == "__main__":
    unittest.main()

