"""``analyze()`` as a whole: targets, selection, limits, and the promise that nothing is executed."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from static_support import FIXTURES, agent, analyze_source, fixture, properties, tool

import pydantic_ai_viz
from pydantic_ai_viz import Analysis, Limits, analyze
from pydantic_ai_viz._analyze import TargetError
from pydantic_ai_viz._analyze._locate import split_target
from pydantic_ai_viz._analyze._resolve import Project, Resolver
from pydantic_ai_viz._analyze._source import Module, parse_module

TEAM = """
from pydantic_ai import Agent, RunContext

helper = Agent("test", name="helper")
lead = Agent("test", name="lead", instructions="Lead the work.")
bystander = Agent("test")


@lead.tool
async def ask_helper(ctx: RunContext[None], question: str) -> str:
    \"\"\"Ask the helper.\"\"\"
    return (await helper.run(question)).output


def not_an_agent():
    return 1
"""


def write(directory: Path, name: str, source: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


# ------------------------------------------------------------------ targets


def test_nothing_to_analyse_is_an_empty_result() -> None:
    assert analyze() == Analysis(agents=[], graphs=[], notices=[], files=[])
    assert analyze().blueprints == []


def test_a_path_object_or_a_string_names_a_file(tmp_path: Path) -> None:
    path = write(tmp_path, "team.py", TEAM)
    assert [b.label for b in analyze(path).agents] == ["helper", "lead", "bystander"]
    assert [b.label for b in analyze(str(path)).agents] == ["helper", "lead", "bystander"]
    assert analyze(path).files == ["team.py"]
    assert analyze(path).blueprints == analyze(path).agents


def test_several_files_are_analysed_together_and_named_from_their_common_directory(tmp_path: Path) -> None:
    first = write(tmp_path, "app/agents/team.py", TEAM)
    second = write(tmp_path, "app/other.py", "from pydantic_ai import Agent\nsolo = Agent('test', name='solo')\n")
    found = analyze(first, second, first)  # the repeat is read once
    assert found.files == ["agents/team.py", "other.py"]
    assert [(b.id, b.label, b.location and b.location.file) for b in found.agents] == [
        ("a1", "helper", "agents/team.py"),
        ("a2", "lead", "agents/team.py"),
        ("a3", "bystander", "agents/team.py"),
        ("a4", "solo", "other.py"),
    ]


def test_a_name_after_the_path_selects_one_agent_and_the_agents_it_delegates_to(tmp_path: Path) -> None:
    path = write(tmp_path, "team.py", TEAM)
    found = analyze(f"{path}:lead")
    # The selected agent first, then its delegate, numbered as shown.
    assert [(b.id, b.label) for b in found.agents] == [("a1", "lead"), ("a2", "helper")]
    assert [(d.tool_name, d.agent_id) for d in found.agents[0].delegates] == [("ask_helper", "a2")]
    assert [(b.id, b.label) for b in analyze(f"{path}:bystander").agents] == [("a1", "bystander")]
    assert [(b.id, b.label) for b in analyze(f"{path}:helper", f"{path}:lead", f"{path}:helper").agents] == [
        ("a1", "helper"),
        ("a2", "lead"),
    ]


def test_a_selection_and_a_whole_file_can_be_mixed(tmp_path: Path) -> None:
    team = write(tmp_path, "team.py", TEAM)
    other = write(tmp_path, "other.py", "from pydantic_ai import Agent\nsolo = Agent('test')\nduo = Agent('test')\n")
    found = analyze(f"{team}:bystander", other)
    assert [(b.id, b.label) for b in found.agents] == [("a1", "bystander"), ("a2", "solo"), ("a3", "duo")]
    assert found.files == ["team.py", "other.py"]  # the whole file was still read


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("missing.py", "file not found: missing.py"),
        ("team.py:nope", "no agent or graph is assigned to 'nope' at the top level of team.py"),
        ("team.py:not_an_agent", "no agent or graph is assigned to 'not_an_agent'"),
        ("team.py:ask_helper", "no agent or graph is assigned to 'ask_helper'"),
        ("team.py:1st", "not a name: '1st' in team.py:1st"),
        ("team.py:", "not a name: '' in team.py:"),
        (".", ". is a directory; pass the path of a .py file"),
        ("team", "cannot read 'team' without importing it"),
        ("pkg.module:agent", "use --import to import a module by name (this runs its code)"),
        ("notes.txt", "cannot read 'notes.txt' without importing it"),
    ],
)
def test_a_target_that_names_nothing_is_an_error_that_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, message: str
) -> None:
    write(tmp_path, "team.py", TEAM)
    write(tmp_path, "notes.txt", "not python")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(TargetError) as exc_info:
        analyze(target)
    assert message in str(exc_info.value)
    assert isinstance(exc_info.value, ValueError)  # what the public docstring promises


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("agents.py", ("agents.py", None)),
        ("agents.py:triage", ("agents.py", "triage")),
        ("AGENTS.PY:triage", ("AGENTS.PY", "triage")),
        (r"C:\work\agents.py", (r"C:\work\agents.py", None)),  # the drive colon separates nothing
        (r"C:\work\agents.py:triage", (r"C:\work\agents.py", "triage")),
        ("pkg.module:triage", ("pkg.module:triage", None)),  # not a file: left whole for the caller to refuse
        ("dir.py/agents.py:a", ("dir.py/agents.py", "a")),
    ],
)
def test_the_name_is_split_from_the_path_at_the_last_colon_after_dot_py(
    spec: str, expected: tuple[str, str | None]
) -> None:
    assert split_target(spec) == expected


# ------------------------------------------------------- skipped, not crashed


def test_a_file_that_is_not_python_is_skipped_with_a_notice_and_the_rest_is_analysed(tmp_path: Path) -> None:
    good = write(tmp_path, "good.py", "from pydantic_ai import Agent\nok = Agent('test')\n")
    broken = write(tmp_path, "broken.py", "from pydantic_ai import Agent\nbad = Agent('test'\n")
    binary = tmp_path / "binary.py"
    binary.write_bytes(b"\xff\xfe\x00\x01 not text at all \x80\x81")
    found = analyze(broken, good, binary)
    assert [b.label for b in found.agents] == ["ok"]
    assert found.files == ["good.py"]
    assert len(found.notices) == 2
    assert found.notices[0].startswith("skipped broken.py: it is not valid Python (")
    assert "on line 2" in found.notices[0]
    assert found.notices[1].startswith("skipped binary.py: it is not valid ")


def test_a_selected_name_in_a_skipped_file_is_not_an_error(tmp_path: Path) -> None:
    broken = write(tmp_path, "broken.py", "def (:\n")
    found = analyze(f"{broken}:agent")
    assert (found.blueprints, found.files) == ([], [])
    assert len(found.notices) == 1


def test_limits_on_size_file_count_and_time_stop_the_reading_with_a_notice(tmp_path: Path) -> None:
    paths = [write(tmp_path, f"m{i}.py", f"from pydantic_ai import Agent\na{i} = Agent('test')\n") for i in range(3)]
    assert len(analyze(*paths).agents) == 3

    small = analyze(*paths, limits=Limits(max_file_bytes=10))
    assert small.agents == []
    assert small.notices[0].startswith("skipped m0.py: it is larger than the 10 byte limit")
    assert len(small.notices) == 3

    few = analyze(*paths, limits=Limits(max_files=2))
    assert [b.label for b in few.agents] == ["a0", "a1"]
    assert len(few.notices) == 1
    assert "stopped reading files at the limit" in few.notices[0]
    assert "m2.py" in few.notices[0]

    late = analyze(*paths, limits=Limits(max_seconds=0))
    assert late.agents == []
    assert "stopped reading files at the limit" in late.notices[0]


def test_resolving_stops_at_the_deadline_and_the_result_says_it_is_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = parse_module(b"from pydantic_ai import Agent\na = Agent('test')\n", Path("m.py"), "m.py")
    assert isinstance(module, Module)
    project = Project(root=Path("."))
    project.add(module)
    call = module.calls[0].node
    on_time = Resolver(project, deadline=time.monotonic() + 60)
    assert on_time.callee(call, module.scope) is not None
    assert on_time.timed_out is False
    late = Resolver(project, deadline=time.monotonic() - 1)
    assert late.callee(call, module.scope) is None
    assert late.timed_out is True

    # Through analyze(): the file is read in time, then the clock jumps past the limit.
    path = write(tmp_path, "team.py", TEAM)
    clock = iter([0.0, 0.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock, 1_000.0))
    found = analyze(path, limits=Limits(max_seconds=5))
    assert found.agents == []
    assert found.notices == ["the analysis ran into its time limit of 5 seconds; what is shown is incomplete"]


def test_source_nested_too_deeply_is_skipped_or_cut_short_never_a_crash(tmp_path: Path) -> None:
    header = "from pydantic_ai import Agent\n"
    cases = {
        "parens.py": header + "x = " + "(" * 3000 + "1" + ")" * 3000 + "\n",
        "attributes.py": header + "a = Agent('test', instructions=x" + ".y" * 30000 + ")\n",
        "additions.py": header + "a = Agent('test', instructions=" + " + ".join(["'a'"] * 3000) + ")\n",
        "calls.py": header + "a = Agent('test')" + ".f()" * 3000 + "\n",
        "null.py": header + "a = Agent('te\x00st')\n",
    }
    for name, source in cases.items():
        found = analyze(write(tmp_path, name, source))
        assert found.blueprints == [], name
        assert len(found.notices) == 1, name
        assert found.notices[0].startswith(f"skipped {name}: it is "), name


def test_tangled_source_ends_in_warnings_not_in_a_loop(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic import BaseModel

        a = b
        b = c
        c = a

        def f():
            return g()

        def g():
            return f()

        class Tree(BaseModel):
            children: list["Tree"]
            parent: "Tree | None" = None

        items = [*more]
        more = [*items]

        tangled = Agent(a, instructions=f(), tools=[*items], toolsets=more, capabilities=c, output_type=Tree)

        @f().tool_plain
        def never_registered(): ...
        """,
    )
    (tangled,) = found.agents
    assert tangled.model is not None
    assert (tangled.model.id, tangled.model.expression) == (None, "a")
    assert tangled.tools == []
    assert [i.expression for i in tangled.instructions] == ["f()"]
    assert tangled.output.type_name == "Tree"
    assert len(tangled.warnings) == 5


def test_many_agents_and_registrations_are_read_in_reasonable_time(tmp_path: Path) -> None:
    source = "from pydantic_ai import Agent\n" + "".join(
        f"a{i} = Agent('test', name='n{i}')\n@a{i}.tool_plain\ndef t{i}(x: int) -> int:\n"
        f"    return a{(i + 1) % 300}.run_sync('x')\n"
        for i in range(300)
    )
    start = time.perf_counter()
    found = analyze(write(tmp_path, "many.py", source))
    assert time.perf_counter() - start < 20
    assert len(found.agents) == 300
    assert found.agents[299].delegates[0].agent_id == "a1"


# ------------------------------------------------------- nothing is executed


def test_reading_a_file_runs_none_of_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PVIZ_HOSTILE_MARKER", str(tmp_path / "marker.txt"))
    monkeypatch.delenv("PVIZ_HOSTILE_RAN", raising=False)
    modules_before = set(sys.modules)

    found = fixture("hostile_side_effects.py")  # it would also never return: it ends in `while True`

    assert list(tmp_path.iterdir()) == []  # no marker, no file from a child process or a shell
    assert "PVIZ_HOSTILE_RAN" not in os.environ
    assert not {name for name in set(sys.modules) - modules_before if "hostile" in name}
    assert found.notices == []
    suspicious = agent(found, "suspicious")
    # What the file computes is reported as the text that computes it.
    assert (
        suspicious.instructions[0].expression
        == 'str(__import__("os").system("echo executed > pviz-hostile-shell.txt"))'
    )
    assert suspicious.settings.expressions == {
        "tool_retries": """eval("__import__('os').getpid()")""",
        "output_retries": """eval("__import__('os').getpid()")""",
        "model_settings.temperature": 'exec("raise SystemExit(3)")',
    }
    assert suspicious.settings.model_settings == {}
    # The computed default is not evaluated: the parameter is optional, and no default is claimed.
    schema = tool(suspicious, "harmless").parameters_schema
    assert properties(schema) == {"path": {"description": "Where to look.", "type": "string"}}
    assert isinstance(schema, dict)
    assert "required" not in schema


def test_the_analysed_code_needs_none_of_its_imports_installed(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        import os
        from dotenv_that_is_not_installed import load_dotenv
        from a_private_package.llm import get_model
        from pydantic_ai import Agent

        load_dotenv()
        os.environ["SECRET"] = "x"

        agent = Agent(get_model(), name="needs_nothing", instructions="Still readable.")
        """,
    )
    (needs_nothing,) = found.agents
    assert needs_nothing.name == "needs_nothing"
    assert needs_nothing.model is not None
    assert (needs_nothing.model.id, needs_nothing.model.expression) == (None, "get_model()")
    assert needs_nothing.warnings == ["the model is set by `get_model()`, so it is only known when the code runs"]
    assert "SECRET" not in os.environ


def test_analysis_does_not_import_pydantic_ai_agents_from_the_target_in_a_clean_process(tmp_path: Path) -> None:
    """In a fresh interpreter: the target is not in ``sys.modules`` and its side effect did not happen."""
    target = write(
        tmp_path,
        "side_effect.py",
        """
        import pathlib
        pathlib.Path(__file__).with_name("ran.txt").write_text("ran")
        from pydantic_ai import Agent
        quiet = Agent("test", name="quiet")
        """,
    )
    probe = (
        "import sys, pydantic_ai_viz as pviz\n"
        f"found = pviz.analyze({str(target)!r})\n"
        "print([b.name for b in found.agents], 'side_effect' in sys.modules)\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "['quiet'] False"
    assert not (tmp_path / "ran.txt").exists()


def test_the_analyser_has_no_way_to_run_code() -> None:
    """A guard for whoever edits the package next: no eval, exec, compile-to-code or import of targets."""
    package = Path(pydantic_ai_viz.__file__).parent / "_analyze"
    banned_names = {"eval", "exec", "compile", "__import__"}
    banned_methods = {"literal_eval", "run_path", "run_module", "exec_module", "load_module", "system", "popen"}
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = node.func
                if isinstance(function, ast.Name):
                    assert function.id not in banned_names | banned_methods, f"{path.name}:{node.lineno}"
                elif isinstance(function, ast.Attribute):  # `re.compile` is fine; `ast.literal_eval` is not
                    assert function.attr not in banned_methods, f"{path.name}:{node.lineno}"
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [alias.name for alias in node.names]
                source = getattr(node, "module", None) or ""
                assert "importlib.util" not in [source, *names], f"{path.name}:{node.lineno}"
                assert not {"runpy", "subprocess"} & {source, *names}, f"{path.name}:{node.lineno}"
    # The one dynamic import is griffe, a dependency of Pydantic AI, for docstrings.
    docstring = (package / "_docstring.py").read_text(encoding="utf-8")
    assert docstring.count("import_module(") == 1
    assert 'import_module("griffe")' in docstring
    assert FIXTURES.is_dir()
