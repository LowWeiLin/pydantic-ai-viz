"""Reading files and tracing names: the ground the analyser stands on."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from pydantic_ai_viz._analyze._resolve import (
    END_OF_MODULE,
    Definition,
    Expression,
    External,
    ModuleRef,
    Project,
    Resolved,
    Resolver,
)
from pydantic_ai_viz._analyze._source import (
    MAX_EXPRESSION_CHARS,
    Module,
    SkippedFile,
    clip_expression,
    module_name,
    parse_module,
    read_module,
)
from pydantic_ai_viz._analyze._values import NotLiteral, fstring_template, literal, string


def parse(source: str, path: Path | None = None) -> Module:
    path = path or Path("module.py")
    module = parse_module(textwrap.dedent(source).encode("utf-8"), path, path.name)
    assert isinstance(module, Module), module
    return module


def resolver_for(*modules: Module) -> Resolver:
    project = Project(root=modules[0].path.parent)
    for module in modules:
        project.add(module)
    return Resolver(project)


def resolve(source: str, name: str) -> Resolved:
    """What ``name`` stands for once the module has run."""
    module = parse(source)
    return resolver_for(module).resolve(ast.Name(id=name, ctx=ast.Load()), module.scope, END_OF_MODULE)


def source_of(target: Resolved) -> str:
    assert isinstance(target, Expression), target
    return target.module.text(target.node)


# ------------------------------------------------------------------ reading


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"def broken(:\n    pass\n", "it is not valid Python (invalid syntax on line 1)"),
        (b"x = 1\n\x00y = 2\n", "it is not valid Python"),
        (b"x = '\xff\xfe'\n", "it is not valid UTF-8 and declares no encoding that decodes it"),
        (b"# -*- coding: no-such-codec -*-\nx = 1\n", "it is not valid UTF-8 and declares no encoding"),
        (b"x = " + b"(" * 5000 + b")" * 5000 + b"\n", "it is n"),  # too many parentheses, or nested too deeply
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "it is not valid"),
    ],
    ids=["syntax", "null-byte", "bad-utf8", "bad-cookie", "deep", "binary"],
)
def test_a_file_that_cannot_be_parsed_is_skipped_with_a_reason(data: bytes, reason: str) -> None:
    result = parse_module(data, Path("odd.py"), "odd.py")
    assert isinstance(result, SkippedFile)
    assert result.rel == "odd.py"
    assert result.reason.startswith(reason), result.reason


def test_declared_encodings_a_bom_and_any_line_ending_are_read() -> None:
    latin = parse_module("# -*- coding: latin-1 -*-\nname = 'caf\xe9'\n".encode("latin-1"), Path("l.py"), "l.py")
    assert isinstance(latin, Module)
    assert latin.lines[1] == "name = 'caf\xe9'"

    bom = parse_module(b"\xef\xbb\xbfvalue = 1\n", Path("b.py"), "b.py")
    assert isinstance(bom, Module)
    assert bom.lines[0] == "value = 1"

    crlf = parse_module(b"a = 1\r\nb = 2\rc = 3\n", Path("c.py"), "c.py")
    assert isinstance(crlf, Module)
    assert [binding[0].line for binding in crlf.scope.bindings.values()] == [1, 2, 3]


def test_a_file_over_the_size_limit_or_unreadable_is_skipped(tmp_path: Path) -> None:
    big = tmp_path / "big.py"
    big.write_bytes(b"x = 1\n" * 100)
    result = read_module(big, "big.py", max_bytes=100)
    assert isinstance(result, SkippedFile)
    assert result.reason == "it is larger than the 100 byte limit (600 bytes)"
    assert isinstance(read_module(big, "big.py", max_bytes=600), Module)

    missing = read_module(tmp_path / "gone.py", "gone.py", max_bytes=1000)
    assert isinstance(missing, SkippedFile)
    assert missing.reason.startswith("it cannot be read")
    a_directory = read_module(tmp_path, "dir.py", max_bytes=10**9)
    assert isinstance(a_directory, SkippedFile)


def test_an_empty_file_and_a_file_of_comments_parse_to_nothing() -> None:
    assert parse("").scope.bindings == {}
    assert parse("# just a comment\n").calls == []


def test_source_text_is_cut_by_utf8_columns_and_put_on_one_line() -> None:
    module = parse(
        """
        greeting = make("héllo wörld", "日本語") + other
        long = call(
            first,
            second,
        )
        """
    )
    greeting, long = (bindings[0].node for bindings in module.scope.bindings.values())
    assert isinstance(greeting, ast.BinOp)
    assert module.text(greeting.left) == 'make("héllo wörld", "日本語")'
    assert module.text(greeting.right) == "other"
    assert long is not None
    assert module.text(long) == "call( first, second, )"
    assert module.raw_text(long).count("\n") == 3
    assert module.text(ast.Name(id="synthetic", ctx=ast.Load())) == "..."


def test_long_source_text_is_clipped() -> None:
    text = clip_expression("f(" + ", ".join(["argument"] * 100) + ")")
    assert len(text) == MAX_EXPRESSION_CHARS
    assert text.endswith("...")
    assert clip_expression("a  +\n   b") == "a + b"
    assert clip_expression("abcdefghij", limit=6) == "abc..."


def test_module_names_follow_the_package_directories(tmp_path: Path) -> None:
    package = tmp_path / "shop" / "agents"
    package.mkdir(parents=True)
    (tmp_path / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    assert module_name(package / "triage.py") == "shop.agents.triage"
    assert module_name(package / "__init__.py") == "shop.agents"
    assert module_name(tmp_path / "script.py") == "script"
    assert module_name(tmp_path / "01_tools.py") == "01_tools"


# -------------------------------------------------------------------- names


def test_names_are_followed_through_aliases_to_the_expression_they_stand_for() -> None:
    source = """
        first = build("a")
        second = first
        third = second
        left, right = make("l"), make("r")
        x, *rest = values()
    """
    assert source_of(resolve(source, "third")) == 'build("a")'
    assert source_of(resolve(source, "right")) == 'make("r")'
    assert resolve(source, "rest") is None  # unpacking is not followed
    assert resolve(source, "never_bound") is None


def test_names_bound_by_running_code_are_unknown() -> None:
    source = """
        for item in items():
            pass
        with open("f") as handle:
            pass
        try:
            pass
        except Exception as error:
            pass
        total = 0
        total += 1
        if (walrus := compute()):
            pass
        match command:
            case [first, *others]:
                pass
            case {"key": value, **remaining}:
                pass
            case captured:
                pass
    """
    for name in ("item", "handle", "error", "walrus", "first", "others", "value", "remaining", "captured"):
        assert resolve(source, name) is None, name
    assert resolve(source, "total") is None  # the augmented assignment wins


def test_the_binding_closest_above_a_use_wins_and_a_later_use_sees_the_last() -> None:
    module = parse(
        """
        thing = make("one")
        use_one = thing
        thing = make("two")
        use_two = thing

        def later():
            return thing
        """
    )
    resolver = resolver_for(module)

    def value_of(name: str) -> str:
        return source_of(resolver.resolve(ast.Name(id=name, ctx=ast.Load()), module.scope, END_OF_MODULE))

    assert value_of("use_one") == 'make("one")'
    assert value_of("use_two") == 'make("two")'
    later = module.function_scopes[id(module.scope.bindings["later"][0].node)]
    assert source_of(resolver.resolve(later.returns[0], later)) == 'make("two")'


def test_a_value_that_refers_to_its_own_name_means_the_earlier_binding() -> None:
    source = """
        agent = make()
        agent = wrap(agent)
        loop = loop
        ping = pong
        pong = ping
    """
    assert source_of(resolve(source, "agent")) == "wrap(agent)"
    assert resolve(source, "loop") is None
    assert resolve(source, "ping") is None  # a cycle ends, it does not hang


def test_functions_classes_and_factories() -> None:
    source = """
        def one_return():
            built = make("x")
            return built

        def two_returns(flag):
            if flag:
                return make("a")
            return make("b")

        def no_return():
            make("c")

        class Box:
            inside = make("boxed")

        a = one_return()
        b = two_returns(True)
        c = no_return()
        d = Box.inside
        e = Box
        f = one_return
        g = await_me()
    """
    assert source_of(resolve(source, "a")) == 'make("x")'
    assert source_of(resolve(source, "b")) == "two_returns(True)"  # which return applies is not knowable
    assert source_of(resolve(source, "c")) == "no_return()"
    assert source_of(resolve(source, "d")) == 'make("boxed")'
    box, function = resolve(source, "e"), resolve(source, "f")
    assert isinstance(box, Definition)
    assert isinstance(function, Definition)
    assert (box.qualified_name, function.qualified_name) == ("module.Box", "module.one_return")


def test_qualified_names_match_what_python_reports() -> None:
    module = parse(
        """
        def outer():
            def inner():
                class Local:
                    def method(self): ...
        class Top:
            def method(self):
                def nested(): ...
        """
    )
    names = sorted(scope.qualname for scope in [*module.function_scopes.values(), *module.class_scopes.values()])
    assert names == [
        "Top",
        "Top.method",
        "Top.method.<locals>.nested",
        "outer",
        "outer.<locals>.inner",
        "outer.<locals>.inner.<locals>.Local",
        "outer.<locals>.inner.<locals>.Local.method",
    ]


def test_scopes_follow_python_rules() -> None:
    module = parse(
        """
        value = make("module")

        class Holder:
            value = make("class")

            def method(self):
                return value        # class bodies are not enclosing scopes for their methods

            def shadow(self, value):
                return value        # a parameter is unknown

        def outer():
            value = make("outer")

            def inner():
                return value

            return inner
        """
    )
    resolver = resolver_for(module)
    returns = {scope.qualname: scope for scope in module.function_scopes.values()}

    def returned(qualname: str) -> Resolved:
        scope = returns[qualname]
        return resolver.resolve(scope.returns[0], scope)

    assert source_of(returned("Holder.method")) == 'make("module")'
    assert returned("Holder.shadow") is None
    assert source_of(returned("outer.<locals>.inner")) == 'make("outer")'


def test_self_attributes_are_followed_inside_the_class_and_its_closures() -> None:
    module = parse(
        """
        class Team:
            shared = make("shared")

            def __init__(self):
                self.lead = make("lead")

                def closure():
                    return self.lead

            def method(self):
                return self.lead

            def from_class(self):
                return self.shared

            def missing(self):
                return self.nothing

            @staticmethod
            def static(self):
                return self.lead
        """
    )
    resolver = resolver_for(module)
    scopes = {scope.qualname: scope for scope in module.function_scopes.values()}

    def returned(qualname: str) -> Resolved:
        return resolver.resolve(scopes[qualname].returns[0], scopes[qualname])

    assert source_of(returned("Team.method")) == 'make("lead")'
    assert source_of(returned("Team.__init__.<locals>.closure")) == 'make("lead")'
    assert source_of(returned("Team.from_class")) == 'make("shared")'
    assert returned("Team.missing") is None
    assert returned("Team.static") is None  # no instance there


def test_attributes_and_methods_of_an_instance_of_a_project_class_are_followed() -> None:
    source = """
        from dataclasses import dataclass
        from pydantic import BaseModel
        from pydantic_settings import BaseSettings

        class Base:
            model = "openai:gpt-5.2"

        class Plain(Base):
            def __init__(self):
                self.made = make("in init")
                self.twice = "a"

            def reset(self):
                self.twice = "b"

            def build(self):
                return make("built")

        class FromEnvironment(BaseSettings):
            model: str = "openai:gpt-5.2"

        class Model(BaseModel):
            model: str = "openai:gpt-5.2"

        @dataclass
        class Data:
            model: str = "openai:gpt-5.2"

        plain = Plain()
        inherited = plain.model
        made = plain.made
        twice = plain.twice
        built = Plain().build()
        method = plain.build
        missing = plain.nothing
        from_environment = FromEnvironment().model
        from_model = Model(model="other").model
        from_data = Data().model
        method_of_model = Model().build
    """
    assert source_of(resolve(source, "inherited")) == '"openai:gpt-5.2"'
    assert source_of(resolve(source, "made")) == 'make("in init")'
    assert source_of(resolve(source, "built")) == 'make("built")'  # what the method returns
    method = resolve(source, "method")
    assert isinstance(method, Definition)
    assert method.node.name == "build"
    # Set in two methods: which value an instance has depends on what ran.
    assert source_of(resolve(source, "twice")) == "plain.twice"
    assert source_of(resolve(source, "missing")) == "plain.nothing"
    # A class that a library shapes takes its fields from the constructor or the environment, not its body.
    for name, text in (
        ("from_environment", "FromEnvironment().model"),
        ("from_model", 'Model(model="other").model'),
        ("from_data", "Data().model"),
        ("method_of_model", "Model().build"),
    ):
        assert source_of(resolve(source, name)) == text


def test_an_item_of_a_written_out_container_is_followed() -> None:
    source = """
        KEY = "fast"
        MODELS = {"fast": "openai:gpt-5-mini", "slow": "openai:gpt-5.2", "fast": "test"}
        PAIR = ("first", "second")
        GROWN = {"a": "x"}
        GROWN["b"] = "y"
        SPREAD = {**MODELS, "own": "z"}

        by_literal = MODELS["slow"]
        by_name = MODELS[KEY]  # a key written twice holds the later value
        by_index = PAIR[-1]
        out_of_range = PAIR[2]
        missing = MODELS["none"]
        changed = GROWN["a"]
        spread = SPREAD["own"]
        computed = MODELS[pick()]
        sliced = PAIR[0:1]
        external = os.environ["MODEL"]
    """
    assert source_of(resolve(source, "by_literal")) == '"openai:gpt-5.2"'
    assert source_of(resolve(source, "by_name")) == '"test"'
    assert source_of(resolve(source, "by_index")) == '"second"'
    for name, text in (
        ("out_of_range", "PAIR[2]"),
        ("missing", 'MODELS["none"]'),
        ("changed", 'GROWN["a"]'),  # changed in place after it was written
        ("spread", 'SPREAD["own"]'),
        ("computed", "MODELS[pick()]"),
        ("sliced", "PAIR[0:1]"),
        ("external", 'os.environ["MODEL"]'),
    ):
        assert source_of(resolve(source, name)) == text


def test_imports_lead_to_external_names() -> None:
    source = """
        import os
        import os.path
        import xml.etree.ElementTree as ET
        import pydantic_ai as pai
        from pydantic_ai import Agent as A, RunContext
        from pydantic_ai.toolsets.function import FunctionToolset
        from . import sibling
        from .near import thing
        from ...far.away import other

        a = os.getenv
        b = os.path.join
        c = ET.parse
        d = pai.Agent
        e = A
        f = FunctionToolset
        g = sibling.value
        h = thing
        i = other
        j = len
    """
    expected = {
        "a": "os.getenv",
        "b": "os.path.join",
        "c": "xml.etree.ElementTree.parse",
        "d": "pydantic_ai.Agent",
        "e": "pydantic_ai.Agent",
        "f": "pydantic_ai.toolsets.function.FunctionToolset",
        "g": ".sibling.value",  # outside any known package, a relative import keeps its dots
        "h": ".near.thing",
        "i": "...far.away.other",
        "j": "builtins.len",
    }
    for name, dotted in expected.items():
        target = resolve(source, name)
        assert isinstance(target, External), name
        assert target.name == dotted, name
    agent = resolve(source, "e")
    assert isinstance(agent, External)
    assert agent.is_library("Agent")
    assert agent.last == "Agent"
    toolset = resolve(source, "f")
    assert isinstance(toolset, External)
    assert toolset.is_library("FunctionToolset", "Agent")
    getenv = resolve(source, "a")
    assert isinstance(getenv, External)
    assert not getenv.is_library("getenv")
    assert getenv.comes_from("os", "getenv")


def test_relative_imports_are_made_absolute_when_the_package_is_known(tmp_path: Path) -> None:
    package = tmp_path / "shop" / "agents"
    package.mkdir(parents=True)
    for directory in (tmp_path / "shop", package):
        (directory / "__init__.py").write_text("", encoding="utf-8")
    module = parse("from .tools import search\nfrom .. import config\nfound = search\n", package / "triage.py")
    resolver = resolver_for(module)
    target = resolver.resolve(ast.Name(id="found", ctx=ast.Load()), module.scope, END_OF_MODULE)
    assert isinstance(target, External)
    assert target.name == "shop.agents.tools.search"
    config = resolver.resolve(ast.Name(id="config", ctx=ast.Load()), module.scope, END_OF_MODULE)
    assert isinstance(config, External)
    assert config.name == "shop.config"


def test_a_star_import_from_pydantic_ai_provides_its_names() -> None:
    target = resolve("from pydantic_ai import *\nfrom os import *\nfound = Agent\nmissing = getenv\n", "found")
    assert isinstance(target, External)
    assert target.name == "pydantic_ai.Agent"
    assert resolve("from os import *\nmissing = getenv\n", "missing") is None


def test_imports_between_analysed_files_are_followed(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    llm = parse("def get_model():\n    return make_model()\nDEFAULT = 'openai:gpt-5.2'\n", tmp_path / "llm.py")
    deep = parse("VALUE = make('deep')\n", tmp_path / "pkg" / "deep.py")
    init = parse("from .deep import VALUE as exported\n", tmp_path / "pkg" / "__init__.py")
    main = parse(
        """
        import llm
        import pkg
        import pkg.deep
        from llm import DEFAULT, get_model as factory
        from pkg import deep as deep_module, exported
        from missing import nothing

        a = DEFAULT
        b = factory
        c = llm.DEFAULT
        d = factory()
        e = pkg.deep.VALUE
        f = deep_module.VALUE
        g = exported
        h = llm
        i = nothing
        j = llm.not_there
        """,
        tmp_path / "main.py",
    )
    resolver = resolver_for(main, llm, deep, init)

    def at_end(name: str) -> Resolved:
        return resolver.resolve(ast.Name(id=name, ctx=ast.Load()), main.scope, END_OF_MODULE)

    assert source_of(at_end("a")) == "'openai:gpt-5.2'"
    assert source_of(at_end("c")) == "'openai:gpt-5.2'"
    function = at_end("b")
    assert isinstance(function, Definition)
    assert function.qualified_name == "llm.get_model"
    assert source_of(at_end("d")) == "make_model()"  # a factory in another file
    for name in ("e", "f", "g"):
        assert source_of(at_end(name)) == "make('deep')", name
    module_ref = at_end("h")
    assert isinstance(module_ref, ModuleRef)
    assert module_ref.module is llm
    missing = at_end("i")
    assert isinstance(missing, External)
    assert missing.name == "missing.nothing"
    assert at_end("j") is None


def test_resolution_is_bounded_however_the_names_are_tangled() -> None:
    chain = "\n".join(f"n{index + 1} = n{index}" for index in range(200))
    assert resolve(f"n0 = make()\n{chain}\n", "n20") is not None
    assert resolve(f"n0 = make()\n{chain}\n", "n200") is None  # too many hops to follow
    nested = "f = lambda: 0\nx = " + "f(" * 40 + ")" * 40 + "()\n"
    assert resolve(nested, "x") is not None
    mutual = "def a():\n    return b()\ndef b():\n    return a()\nx = a()\n"
    assert resolve(mutual, "x") is None
    attribute_chain = "import os\nx = os" + ".a" * 300 + "\n"
    target = resolve(attribute_chain, "x")
    assert isinstance(target, External)
    assert target.name.count(".a") == 300


def test_chains_too_long_for_the_parser_skip_the_file() -> None:
    for source in ("import os\nx = os" + ".a" * 100_000 + "\n", "x = " + " + ".join(["'x'"] * 100_000) + "\n"):
        result = parse_module(source.encode("utf-8"), Path("long.py"), "long.py")
        if isinstance(result, SkippedFile):  # this Python's parser gave up, and said so
            assert result.reason.startswith("it is nested too deeply")
        else:  # a parser that copes leaves a module the analyser must cope with too
            name = ast.Name(id="x", ctx=ast.Load())
            resolver_for(result).resolve(name, result.scope, END_OF_MODULE)


# ------------------------------------------------------------------- values


def value(source: str, name: str = "value") -> object:
    module = parse(source)
    return literal(resolver_for(module), ast.Name(id=name, ctx=ast.Load()), module.scope, END_OF_MODULE)


def test_literals_are_read() -> None:
    source = """
        BASE = "You are"
        value = {
            "text": BASE + " helpful" " and brief",
            "number": -3,
            "ratio": +0.5,
            "flag": True,
            "nothing": None,
            "items": [1, (2, 3), {4}],
            "nested": {"deep": ["x"]},
            "plain_f": f"no placeholders",
        }
    """
    assert value(source) == {
        "text": "You are helpful and brief",
        "number": -3,
        "ratio": 0.5,
        "flag": True,
        "nothing": None,
        "items": [1, [2, 3], [4]],
        "nested": {"deep": ["x"]},
        "plain_f": "no placeholders",
    }


def test_the_strip_methods_of_a_literal_string_are_applied() -> None:
    source = """
        value = ["  padded  ".strip(), "  padded  ".lstrip(), ("  padded  " "!  ").rstrip()]
    """
    assert value(source) == ["padded", "padded  ", "  padded  !"]


def test_a_call_of_a_library_function_is_not_evaluated_even_when_it_is_pure() -> None:
    # ``textwrap.dedent`` would give the same text every time; it is still code outside the source.
    for call in ('dedent("  x")', 'textwrap.dedent("  x")', 'inspect.cleandoc("x")', 'dedent("  x").strip()'):
        with pytest.raises(NotLiteral):
            value(f"import inspect, textwrap\nfrom textwrap import dedent\nvalue = {call}\n")


def test_an_f_string_whose_placeholders_are_literals_is_folded() -> None:
    source = """
        PROVIDER = "openai"
        NAME = "gpt-5.2"
        TIER = 2
        value = [f"{PROVIDER}:{NAME}", f"tier {TIER}, ratio {0.5}", f"{NAME!r}", f"{'é'!a}", f"{PROVIDER + ':x'}"]
    """
    assert value(source) == ["openai:gpt-5.2", "tier 2, ratio 0.5", "'gpt-5.2'", "'\\xe9'", "openai:x"]


@pytest.mark.parametrize(
    "expression",
    [
        'f"hello {name}"',
        'f"{3:>5}"',  # a format spec is not applied
        'f"{[1, 2]}"',  # nor is a container written out as text
        'f"{os.environ}"',
        "compute()",
        "os.environ['X']",
        "1 + 2",
        "[1, *more]",
        "{**base}",
        "{1: 'int key'}",
        "b'bytes'",
        "...",
        "1j",
        "-'text'",
        "not True",
        "'a' * 3",
        "'x'.strip('x')",
        "'x'.upper()",
        "str('x')",
        "dedent('x', 'y')",
        "float('inf')",
        "10 ** 400",
        "9" * 30,
        "lambda: 1",
        "'%s' % 'x'",
        "'{}'.format('x')",
        "unbound_name",
    ],
)
def test_anything_that_needs_evaluating_is_not_a_literal(expression: str) -> None:
    with pytest.raises(NotLiteral):
        value(f"from textwrap import dedent\nimport os\nvalue = {expression}\n")


def test_a_name_assigned_more_than_once_is_not_a_literal() -> None:
    with pytest.raises(NotLiteral):
        value("PROMPT = 'a'\nPROMPT = 'b'\nvalue = PROMPT\n")
    assert value("PROMPT = 'a'\nvalue = PROMPT\n") == "a"


def test_literal_reading_gives_up_on_anything_enormous() -> None:
    deep = "value = " + "[" * 60 + "]" * 60 + "\n"
    with pytest.raises(NotLiteral):
        value(deep)
    wide = "value = [" + ", ".join(["0"] * 6000) + "]\n"
    with pytest.raises(NotLiteral):
        value(wide)
    long_text = "value = '" + "x" * 250_000 + "'\n"
    with pytest.raises(NotLiteral):
        value(long_text)
    pieces = "value = " + " + ".join(["'x'"] * 400) + "\n"
    assert value(pieces) == "x" * 400
    long_pieces = "value = " + " + ".join(["'" + "x" * 1000 + "'"] * 400) + "\n"
    with pytest.raises(NotLiteral):
        value(long_pieces)
    module = parse("value = 'short'\n")
    assert string(resolver_for(module), ast.Name(id="value", ctx=ast.Load()), module.scope, END_OF_MODULE) == "short"
    assert string(resolver_for(module), ast.Constant(value=3), module.scope, 1) is None


def test_an_f_string_becomes_a_template() -> None:
    module = parse(
        """
        value = f"Hi {user.name!r}, you have {count:>4} items{suffix} " f"and {total:{width}.2f} EUR{{literal}}"
        """
    )
    node = module.scope.bindings["value"][0].node
    assert isinstance(node, ast.JoinedStr)
    assert fstring_template(node) == (
        "Hi {user.name!r}, you have {count:>4} items{suffix} and {total:{...}.2f} EUR{literal}"
    )
