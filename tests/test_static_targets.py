"""What a target can be besides one file: a directory, a module name, and the files that imports lead to."""

from __future__ import annotations

import os
import sys
import textwrap
import time
from pathlib import Path

import pytest
from static_support import properties, tool

from pydantic_ai_viz import Limits, analyze
from pydantic_ai_viz._analyze import TargetError
from pydantic_ai_viz._analyze._locate import MAX_DIRECTORY_DEPTH, project_root, python_files
from pydantic_ai_viz._models import SourceLocation

ONE_AGENT = "from pydantic_ai import Agent\n{name} = Agent('test', name='{name}')\n"


def write(root: Path, name: str, source: str = "") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def one_agent(root: Path, name: str, label: str) -> Path:
    return write(root, name, ONE_AGENT.format(name=label))


def labels(found: object) -> list[str]:
    return [blueprint.label for blueprint in found.agents]  # type: ignore[attr-defined]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A directory that is recognisably a project: imports are followed anywhere inside it."""
    root = tmp_path / "proj"
    write(root, "pyproject.toml", "[project]\nname = 'proj'\n")
    return root


# ---------------------------------------------------------------- directories


def test_a_directory_is_read_all_the_way_down_in_a_stable_order(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    one_agent(root, "zeta.py", "zeta")
    one_agent(root, "alpha.py", "alpha")
    one_agent(root, "pkg/beta.py", "beta")
    one_agent(root, "pkg/deep/er/gamma.py", "gamma")
    one_agent(root, "aaa/delta.py", "delta")
    write(root, "notes.txt", "not python")
    write(root, "pkg/data.json", "{}")
    found = analyze(root)
    # A directory's own files first, by name; then its subdirectories, by name.
    assert found.files == ["alpha.py", "zeta.py", "aaa/delta.py", "pkg/beta.py", "pkg/deep/er/gamma.py"]
    assert [(b.id, b.label) for b in found.agents] == [
        ("a1", "alpha"),
        ("a2", "zeta"),
        ("a3", "delta"),
        ("a4", "beta"),
        ("a5", "gamma"),
    ]
    assert found.agents[4].location == SourceLocation(file="pkg/deep/er/gamma.py", line=2)
    assert (found.title, found.notices) == ("shop", [])
    assert analyze(str(root)).files == found.files
    assert analyze(str(root) + os.sep).files == found.files


def test_directories_of_installed_and_generated_code_are_passed_over(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    one_agent(root, "app.py", "mine")
    for skipped in (
        ".venv/lib/x.py",
        "venv/lib/x.py",
        "node_modules/pkg/x.py",
        "__pycache__/x.py",
        "site-packages/pkg/x.py",
        "build/lib/x.py",
        "dist/x.py",
        ".git/hooks/x.py",
        ".hidden/x.py",
        "shop.egg-info/x.py",
        "src/nested/node_modules/x.py",
    ):
        one_agent(root, skipped, "not_mine")
    # A virtual environment under any name is known by the file every one of them holds.
    one_agent(root, "my-env/lib/x.py", "not_mine")
    write(root, "my-env/pyvenv.cfg", "home = /usr/bin\n")
    one_agent(root, "src/builder/x.py", "also_mine")  # only the exact names are skipped
    write(root, "dist/readme.txt", "no Python here")
    write(root, "docs/build/index.html", "<p>nor here</p>")
    found = analyze(root)
    assert found.files == ["app.py", "src/builder/x.py"]
    assert labels(found) == ["mine", "also_mine"]
    # A directory called build or dist may hold a package of the project's own: it is named, not dropped silently.
    assert found.notices == [
        "passed over the directory build, which holds .py files: a directory of that name usually holds generated "
        "code; give it as a target to read it",
        "passed over the directory dist, which holds .py files: a directory of that name usually holds generated "
        "code; give it as a target to read it",
    ]


def test_a_directory_named_as_the_target_is_read_whatever_it_is_called(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    one_agent(root, "build/tool.py", "builder")
    one_agent(root, ".hidden/tool.py", "hidden")
    assert labels(analyze(root)) == []
    assert labels(analyze(root / "build")) == ["builder"]
    assert labels(analyze(root / ".hidden")) == ["hidden"]


def test_a_directory_without_python_files_is_an_empty_result_with_a_notice(tmp_path: Path) -> None:
    write(tmp_path, "empty/readme.md", "nothing here")
    found = analyze(tmp_path / "empty")
    assert (found.blueprints, found.files) == ([], [])
    assert found.notices == [f"{tmp_path / 'empty'} holds no .py files"]


def test_a_directory_with_more_files_than_the_limit_is_cut_short_with_a_notice(tmp_path: Path) -> None:
    root = tmp_path / "many"
    for index in range(5):
        one_agent(root, f"m{index}.py", f"agent{index}")
    found = analyze(root, limits=Limits(max_files=3))
    assert found.files == ["m0.py", "m1.py", "m2.py"]
    assert found.notices == ["many holds more than 3 .py files; only the first 3 were read"]
    assert len(found.agents) == 3


def test_looking_for_files_stops_at_the_time_limit_and_at_a_depth_no_project_has(tmp_path: Path) -> None:
    root = tmp_path / "deep"
    one_agent(root, "top.py", "top")
    one_agent(root, "sub/inner.py", "inner")
    files, notices = python_files(root, max_files=10, deadline=time.monotonic() - 1)
    assert files == []
    assert notices == ["stopped looking for files below deep at the time limit; . and what follows were not read"]

    nested = root.joinpath(*["d"] * (MAX_DIRECTORY_DEPTH + 1))
    one_agent(nested, "bottom.py", "bottom")
    files, notices = python_files(root, max_files=10, deadline=time.monotonic() + 60)
    assert [path.name for path in files] == ["top.py", "inner.py"]
    assert len(notices) == 1
    assert notices[0].endswith("/d: it is nested too deeply")


def test_a_link_to_a_directory_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    one_agent(root, "app.py", "mine")
    one_agent(tmp_path, "elsewhere/other.py", "elsewhere")
    link_directory(root / "linked", tmp_path / "elsewhere")
    link_directory(root / "loop", root)
    found = analyze(root)
    assert (found.files, labels(found)) == (["app.py"], ["mine"])


def link_directory(link: Path, target: Path) -> None:
    """A link to a directory: a symbolic link, or on Windows a junction, which needs no privilege."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        pass
    if sys.platform != "win32":
        pytest.skip("this account cannot create symbolic links")
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


SECRET = "SECRET_TOKEN = 'sk-outside-the-target'\n"
USES_SECRET = (
    "from pydantic_ai import Agent\nfrom {module} import SECRET_TOKEN\n"
    "agent = Agent('test', instructions=SECRET_TOKEN)\n"
)


def test_an_import_is_not_followed_through_a_link_that_leads_out_of_the_project(project: Path) -> None:
    write(project.parent, "outside/secret.py", SECRET)
    (project / "pkg").mkdir()
    link_directory(project / "pkg" / "esc", project.parent / "outside")
    write(project, "main.py", USES_SECRET.format(module="pkg.esc.secret"))
    found = analyze(project)
    assert found.files == ["main.py"]
    assert found.agents[0].instructions[0].text is None
    assert "sk-outside-the-target" not in found.agents[0].model_dump_json()
    # Named as a target, the file behind the link is read: the user asked for it.
    assert analyze(project.parent / "outside" / "secret.py").files == ["secret.py"]


def test_a_linked_python_file_is_neither_listed_nor_imported(project: Path) -> None:
    secret = write(project.parent, "outside/secret.py", SECRET)
    try:
        os.symlink(secret, project / "secret.py")
    except OSError:
        pytest.skip("this account cannot create symbolic links to files")
    write(project, "main.py", USES_SECRET.format(module="secret"))
    found = analyze(project)
    assert found.files == ["main.py"]
    assert "sk-outside-the-target" not in found.agents[0].model_dump_json()


def test_broken_files_in_a_directory_are_skipped_and_the_rest_is_read(tmp_path: Path) -> None:
    root = tmp_path / "shop"
    one_agent(root, "good.py", "good")
    write(root, "bad/broken.py", "def (:\n")
    (root / "bad" / "binary.py").write_bytes(b"\xff\xfe\x00 not text \x80")
    found = analyze(root)
    assert labels(found) == ["good"]
    assert found.files == ["good.py"]
    assert [notice.split(":")[0] for notice in found.notices] == ["skipped bad/binary.py", "skipped bad/broken.py"]


# ------------------------------------------------- one project, several files

MODELS = """
from dataclasses import dataclass
from pydantic import BaseModel, Field


class Verdict(BaseModel):
    approved: bool
    reason: str = Field(description="Why.")


@dataclass
class Deps:
    api_key: str
"""

TOOLS = '''
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from .models import Deps

library = FunctionToolset(id="library")


@library.tool
def search(ctx: RunContext[Deps], query: str, limit: int = 5) -> list[str]:
    """Search the library.

    Args:
        query: What to look for.
        limit: How many results.
    """
    return []


def word_count(text: str) -> int:
    """Count the words of a text."""
    return len(text.split())
'''

REVIEWER = """
from pydantic_ai import Agent

from ..models import Deps, Verdict
from ..prompts import REVIEW_PROMPT

reviewer = Agent("openai:gpt-5.2", name="reviewer", deps_type=Deps, output_type=Verdict, instructions=REVIEW_PROMPT)
"""

WRITER = """
from pydantic_ai import Agent, RunContext

from shop import prompts
from shop.agents.reviewer import reviewer
from shop.models import Deps, Verdict
from shop.tools import library, word_count

writer = Agent(
    "openai:gpt-5.2",
    name="writer",
    deps_type=Deps,
    instructions=prompts.WRITE_PROMPT,
    tools=[word_count],
    toolsets=[library],
)


@writer.tool
async def ask_reviewer(ctx: RunContext[Deps], draft: str) -> Verdict:
    return (await reviewer.run(draft, deps=ctx.deps)).output
"""


@pytest.fixture
def shop(project: Path) -> Path:
    """A package laid out the way an application is: models, prompts, tools and agents each in their own file."""
    write(project, "shop/__init__.py")
    write(project, "shop/models.py", MODELS)
    write(project, "shop/prompts.py", 'REVIEW_PROMPT = "Review the draft."\nWRITE_PROMPT = "Write a draft."\n')
    write(project, "shop/tools.py", TOOLS)
    write(project, "shop/agents/__init__.py", "from .reviewer import reviewer\nfrom .writer import writer\n")
    write(project, "shop/agents/reviewer.py", REVIEWER)
    write(project, "shop/agents/writer.py", WRITER)
    return project


def test_agents_tools_toolsets_models_and_delegation_link_up_across_the_files_of_a_directory(shop: Path) -> None:
    found = analyze(shop)
    assert found.notices == []
    assert found.files == [
        "shop/__init__.py",
        "shop/models.py",
        "shop/prompts.py",
        "shop/tools.py",
        "shop/agents/__init__.py",
        "shop/agents/reviewer.py",
        "shop/agents/writer.py",
    ]
    reviewer, writer = found.agents
    assert (reviewer.name, writer.name) == ("reviewer", "writer")

    # Instructions held in a constant of another file, reached by a relative and by an absolute import.
    assert [i.text for i in reviewer.instructions] == ["Review the draft."]
    assert [i.text for i in writer.instructions] == ["Write a draft."]
    # The output model and the dependencies come from a third file.
    assert reviewer.output.type_name == "Verdict"
    assert reviewer.output.location == SourceLocation(file="shop/models.py", line=6)
    assert properties(reviewer.output.json_schema) == {
        "approved": {"type": "boolean"},
        "reason": {"description": "Why.", "type": "string"},
    }
    assert reviewer.deps_type == writer.deps_type == "shop.models.Deps"

    # A plain function and a toolset, both defined in tools.py.
    counted = tool(writer, "word_count")
    assert (counted.function, counted.location) == (
        "shop.tools.word_count",
        SourceLocation(file="shop/tools.py", line=21),
    )
    assert [(ts.kind, ts.toolset_id, ts.location) for ts in writer.toolsets] == [
        ("agent", "<agent>", None),
        ("function", "library", SourceLocation(file="shop/tools.py", line=7)),
    ]
    searched = tool(writer, "search")
    assert searched.toolset == "ts1"
    assert searched.takes_ctx is True
    assert properties(searched.parameters_schema) == {
        "query": {"description": "What to look for.", "type": "string"},
        "limit": {"default": 5, "description": "How many results.", "type": "integer"},
    }
    # The tool defined next to the agent returns the model from the third file, and delegates across files.
    assert tool(writer, "ask_reviewer").return_schema == reviewer.output.json_schema
    assert [(d.tool_name, d.agent_id) for d in writer.delegates] == [("ask_reviewer", reviewer.id)]
    assert writer.warnings == reviewer.warnings == []


def test_one_file_is_read_with_the_files_its_imports_lead_to(shop: Path) -> None:
    found = analyze(shop / "shop" / "agents" / "writer.py")
    # Named relative to the target's own directory; what the imports led to comes after it.
    assert found.files == [
        "writer.py",
        "../__init__.py",
        "../prompts.py",
        "reviewer.py",
        "../models.py",
        "../tools.py",
    ]
    assert found.title == "writer"
    # The file's own agent, and the one it imports by name and delegates to.
    writer, reviewer = found.agents
    assert [(b.id, b.name, b.location) for b in found.agents] == [
        ("a1", "writer", SourceLocation(file="writer.py", line=9)),
        ("a2", "reviewer", SourceLocation(file="reviewer.py", line=7)),
    ]
    assert [(d.tool_name, d.agent_id) for d in writer.delegates] == [("ask_reviewer", "a2")]
    assert tool(writer, "search").location == SourceLocation(file="../tools.py", line=11)
    assert properties(reviewer.output.json_schema)["reason"] == {"description": "Why.", "type": "string"}
    assert writer.warnings == []


def test_a_file_read_only_because_it_is_imported_adds_no_blueprints_of_its_own(project: Path) -> None:
    write(
        project,
        "helpers.py",
        """
        from pydantic_ai import Agent
        from pydantic_graph import GraphBuilder

        MODEL = "openai:gpt-5.2"
        spare = Agent(MODEL, name="spare")
        other = Agent(MODEL, name="other")
        named = Agent(MODEL, name="named")
        flow = GraphBuilder(output_type=int)


        @named.tool_plain
        async def ask_other(question: str) -> str:
            return (await other.run(question)).output
        """,
    )
    main = write(
        project,
        "main.py",
        """
        from pydantic_ai import Agent
        import helpers
        from helpers import MODEL, named as assistant, flow

        mine = Agent(MODEL, name="mine")
        """,
    )
    found = analyze(main)
    assert found.files == ["main.py", "helpers.py"]
    # Its own agent; the one it imports by name; the one that one delegates to. Not the spare one.
    assert [(b.id, b.name) for b in found.agents] == [("a1", "mine"), ("a2", "other"), ("a3", "named")]
    assert found.agents[0].model is not None
    assert found.agents[0].model.id == "openai:gpt-5.2"  # a constant from the imported file
    assert [(d.tool_name, d.agent_id) for d in found.agents[2].delegates] == [("ask_other", "a2")]
    assert [(g.id, g.label) for g in found.graphs] == [("g1", "flow")]
    assert found.notices == [
        "1 agent or graph defined in imported files (helpers.py) are not shown; "
        "name those files or their directory to see them"
    ]
    # Named as a target, the file shows everything it defines.
    assert [b.name for b in analyze(main, project / "helpers.py").agents] == ["mine", "spare", "other", "named"]
    assert analyze(project).notices == []


def test_a_name_selected_in_a_file_may_be_one_it_imports(shop: Path) -> None:
    package = shop / "shop" / "agents" / "__init__.py"
    found = analyze(f"{package}:writer")
    assert [(b.id, b.name) for b in found.agents] == [("a1", "writer"), ("a2", "reviewer")]
    assert [(b.id, b.name) for b in analyze(f"{package}:reviewer").agents] == [("a1", "reviewer")]


def test_imports_are_followed_above_a_directory_target_but_not_out_of_the_project(project: Path) -> None:
    write(project, "shared/models.py", "from pydantic import BaseModel\n\nclass Answer(BaseModel):\n    text: str\n")
    write(project.parent, "outside.py", "from pydantic import BaseModel\n\nclass Outside(BaseModel):\n    x: int\n")
    write(
        project,
        "app/agents.py",
        """
        from pydantic_ai import Agent
        from shared.models import Answer
        from outside import Outside

        inside = Agent("test", name="inside", output_type=Answer)
        beyond = Agent("test", name="beyond", output_type=Outside)
        """,
    )
    found = analyze(project / "app")
    assert found.files == ["agents.py", "../shared/models.py"]
    inside, beyond = found.agents
    assert properties(inside.output.json_schema) == {"text": {"type": "string"}}
    assert inside.output.location == SourceLocation(file="../shared/models.py", line=3)
    assert beyond.output.json_schema is None
    assert beyond.warnings == [
        "the schema of the output type `Outside` cannot be read from the analysed source, so it is not shown"
    ]


def test_without_a_project_file_imports_are_followed_inside_the_target_only(tmp_path: Path) -> None:
    write(tmp_path, "loose/models.py", "MODEL = 'openai:gpt-5.2'\n")
    write(tmp_path, "loose/sub/agent.py", "from pydantic_ai import Agent\nfrom models import MODEL\nx = Agent(MODEL)\n")
    assert project_root(tmp_path / "loose" / "sub") == tmp_path / "loose" / "sub"
    alone = analyze(tmp_path / "loose" / "sub")
    assert alone.files == ["agent.py"]
    assert alone.agents[0].model is not None
    assert alone.agents[0].model.expression == "MODEL"
    both = analyze(tmp_path / "loose")
    assert both.agents[0].model is not None
    assert both.agents[0].model.id == "openai:gpt-5.2"


def test_where_a_project_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path, "repo/.git/HEAD", "ref: refs/heads/main\n")
    write(tmp_path, "repo/services/api/pyproject.toml")
    deep = tmp_path / "repo" / "services" / "api" / "src" / "api" / "agents"
    write(deep, "__init__.py")
    assert project_root(deep) == tmp_path / "repo" / "services" / "api"  # the nearest project file wins
    assert project_root(tmp_path / "repo" / "services") == tmp_path / "repo"
    # No project file: the directory above the outermost package, and failing that the directory itself.
    write(tmp_path, "bare/pkg/__init__.py")
    write(tmp_path, "bare/pkg/sub/__init__.py")
    assert project_root(tmp_path / "bare" / "pkg" / "sub") == tmp_path / "bare"
    assert project_root(tmp_path / "bare") == tmp_path / "bare"
    # A project file in the home directory does not make everything below it one project.
    home = tmp_path / "home"
    write(home, "requirements.txt")
    write(home, "code/thing/x.py")
    monkeypatch.setattr(Path, "home", lambda: home)
    assert project_root(home / "code" / "thing") == home / "code" / "thing"
    assert project_root(deep, within=tmp_path / "repo" / "services" / "api" / "src") == (
        tmp_path / "repo" / "services" / "api" / "src"
    )
    # The home directory itself, or a package directly in it, is never the place imports may go.
    write(home, "pkg/__init__.py")
    assert project_root(home) is None
    assert project_root(home / "pkg") == home / "pkg"


def test_a_file_in_the_home_directory_does_not_open_the_rest_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    write(home, "Documents/work/other/settings.py", "API_KEY = 'sk-live-from-another-project'\n")
    target = write(
        home,
        "agent.py",
        """
        from pydantic_ai import Agent
        from Documents.work.other.settings import API_KEY
        agent = Agent('test', instructions=API_KEY)
        """,
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    found = analyze(target)
    assert found.files == ["agent.py"]
    assert "sk-live" not in found.agents[0].model_dump_json()
    # The same for a directory target that is the home directory: only what lies below it is read.
    assert analyze(home).files == ["agent.py", "Documents/work/other/settings.py"]


def test_installed_packages_are_never_opened_even_when_a_copy_lies_in_the_project(project: Path) -> None:
    # A vendored or shadowing copy of the library: its names keep meaning the library.
    write(project, "pydantic_ai/__init__.py", "class Agent:\n    pass\n")
    write(project, "pydantic/__init__.py", "class BaseModel:\n    pass\n")
    write(project, ".venv/lib/site-packages/helper_lib/__init__.py", "PROMPT = 'from the environment'\n")
    write(project, "node_modules/helper_js/__init__.py", "PROMPT = 'from node'\n")
    target = write(
        project,
        "app.py",
        """
        from pydantic import BaseModel
        from pydantic_ai import Agent
        from helper_lib import PROMPT

        class Out(BaseModel):
            n: int

        found = Agent("test", name="found", output_type=Out, instructions=PROMPT)
        """,
    )
    result = analyze(target)
    assert result.files == ["app.py"]
    (found,) = result.agents
    assert properties(found.output.json_schema) == {"n": {"type": "integer"}}
    assert found.instructions[0].expression == "PROMPT"
    assert found.warnings == [
        "one of the instructions is `PROMPT` (helper_lib.PROMPT), which is defined outside the analysed source; "
        "whether it is text or a function is not known"
    ]


def test_an_absolute_import_is_looked_up_in_every_directory_above_the_importing_file(project: Path) -> None:
    # ``backend`` is a source root nobody declared: the code is started from inside it.
    write(project, "backend/config.py", "MODEL = 'openai:gpt-5.2'\n")
    write(
        project, "backend/app/models.py", "from pydantic import BaseModel\n\nclass Reply(BaseModel):\n    text: str\n"
    )
    write(
        project,
        "backend/app/agents/support.py",
        """
        from pydantic_ai import Agent
        from app.models import Reply
        from config import MODEL

        support = Agent(MODEL, output_type=Reply)
        """,
    )
    (support,) = analyze(project).agents
    assert support.model is not None
    assert support.model.id == "openai:gpt-5.2"
    assert properties(support.output.json_schema) == {"text": {"type": "string"}}
    assert support.warnings == []
    # The same holds when only the one file is named.
    (alone,) = analyze(project / "backend" / "app" / "agents" / "support.py").agents
    assert alone.model is not None
    assert (alone.model.id, alone.warnings) == ("openai:gpt-5.2", [])


def test_a_src_directory_and_a_unique_path_in_another_source_root_are_found(project: Path) -> None:
    write(project, "src/corelib/__init__.py")
    write(project, "src/corelib/prompts.py", "HELLO = 'Say hello.'\n")
    write(project, "packages/extra/extralib/models.py", "MODEL = 'openai:gpt-5.2'\n")
    write(
        project,
        "examples/run.py",
        """
        from pydantic_ai import Agent
        from corelib.prompts import HELLO
        from extralib.models import MODEL

        greeter = Agent(MODEL, instructions=HELLO)
        """,
    )
    # One file: ``src`` is looked in; the other source root is nowhere above the file, so it stays unknown.
    (alone,) = analyze(project / "examples" / "run.py").agents
    assert [i.text for i in alone.instructions] == ["Say hello."]
    assert alone.model is not None
    assert alone.model.expression == "MODEL"
    # The whole project: exactly one file's path ends in extralib/models.py, so that is the one meant.
    (greeter,) = analyze(project).agents
    assert greeter.model is not None
    assert greeter.model.id == "openai:gpt-5.2"
    # Two candidates: no guessing.
    write(project, "packages/other/extralib/models.py", "MODEL = 'anthropic:claude'\n")
    (unsure,) = analyze(project).agents
    assert unsure.model is not None
    assert unsure.model.expression == "MODEL"


def test_a_single_name_is_not_matched_to_a_file_somewhere_else_in_the_project(project: Path) -> None:
    # ``agents`` here is another library. A file of that name in an unrelated corner is not it.
    write(project, "ours/agents.py", "from pydantic_ai import Agent\n")
    write(project, "theirs/main.py", "from agents import Agent\nother = Agent(name='other', instructions='x')\n")
    assert analyze(project).agents == []


def test_the_standard_library_is_shadowed_by_a_script_s_neighbour_only(project: Path) -> None:
    write(project, "scripts/secrets.py", "MODEL = 'openai:gpt-5.2'\n")
    write(project, "scripts/run.py", "from pydantic_ai import Agent\nfrom secrets import MODEL\na = Agent(MODEL)\n")
    write(project, "pkg/__init__.py")
    write(project, "pkg/secrets.py", "MODEL = 'openai:gpt-5.2'\n")
    write(project, "pkg/run.py", "from pydantic_ai import Agent\nfrom secrets import MODEL\nb = Agent(MODEL)\n")
    script, packaged = analyze(project / "scripts" / "run.py", project / "pkg" / "run.py").agents
    assert script.model is not None
    assert script.model.id == "openai:gpt-5.2"
    assert packaged.model is not None
    assert packaged.model.expression == "MODEL"  # inside a package, ``secrets`` is the standard library's


def test_a_name_is_not_mistaken_for_a_file_that_differs_in_case(project: Path) -> None:
    write(project, "tools/__init__.py", "class Search:\n    pass\n")
    write(project, "tools/search.py", "from pydantic_ai import Agent\nhidden = Agent('test', name='hidden')\n")
    target = write(
        project,
        "app.py",
        "from pydantic_ai import Agent\nfrom tools import Search\nmine = Agent('test', name='mine')\n",
    )
    found = analyze(target)
    # ``Search`` is a class in the package, not the module search.py, whatever the file system thinks of case.
    assert found.files == ["app.py", "tools/__init__.py"]
    assert found.notices == []


def test_names_brought_in_by_a_star_import_and_re_exported_by_a_package_are_followed(project: Path) -> None:
    write(project, "kit/__init__.py", "from .prompts import *\nfrom .models import Reply as Answer\n")
    write(project, "kit/prompts.py", "from .base import *\nGREETING = 'Hello.'\n")
    write(project, "kit/base.py", "MODEL = 'openai:gpt-5.2'\n")
    write(project, "kit/models.py", "from pydantic import BaseModel\n\nclass Reply(BaseModel):\n    text: str\n")
    target = write(
        project,
        "app.py",
        """
        from pydantic_ai import *
        from kit import *
        import kit

        starred = Agent(MODEL, instructions=GREETING, output_type=Answer)
        dotted = Agent(kit.MODEL, instructions=kit.GREETING, output_type=kit.Answer)
        """,
    )
    for found in analyze(target).agents:
        assert found.model is not None
        assert found.model.id == "openai:gpt-5.2"
        assert [i.text for i in found.instructions] == ["Hello."]
        assert properties(found.output.json_schema) == {"text": {"type": "string"}}
        assert found.warnings == []


def test_files_that_import_each_other_in_a_circle_end_in_a_warning_not_in_a_loop(project: Path) -> None:
    write(project, "a.py", "from b import thing, MODEL\nfrom b import *\n")
    write(project, "b.py", "from a import thing\nfrom a import *\nfrom c import MODEL\n")
    write(project, "c.py", "from c import MODEL\nimport c\n")
    target = write(
        project,
        "app.py",
        """
        from pydantic_ai import Agent
        from a import thing, MODEL, nowhere

        looped = Agent(MODEL, instructions=thing, tools=[nowhere])
        """,
    )
    found = analyze(target)
    assert sorted(found.files) == ["a.py", "app.py", "b.py", "c.py"]
    (looped,) = found.agents
    assert looped.model is not None
    assert looped.model.expression == "MODEL"
    assert len(looped.warnings) == 3
    assert found.notices == []


def test_following_imports_stops_at_the_file_limit_with_a_notice(project: Path) -> None:
    write(project, "one.py", "from two import TWO\nONE = 'one'\n")
    write(project, "two.py", "from three import THREE\nTWO = 'two'\n")
    write(project, "three.py", "THREE = 'three'\n")
    target = write(
        project, "app.py", "from pydantic_ai import Agent\nfrom one import ONE\na = Agent('test', name=ONE)\n"
    )
    assert analyze(target).files == ["app.py", "one.py", "two.py", "three.py"]
    found = analyze(target, limits=Limits(max_files=2))
    assert found.files == ["app.py", "one.py"]
    assert found.notices == ["stopped following imports at the limit; two.py and other imported files were not read"]
    assert labels(found) == ["one"]


def test_an_imported_file_that_is_not_valid_python_is_skipped_with_a_notice(project: Path) -> None:
    write(project, "broken.py", "def (:\n")
    target = write(
        project, "app.py", "from pydantic_ai import Agent\nfrom broken import MODEL\na = Agent(MODEL, name='a')\n"
    )
    found = analyze(target)
    assert found.files == ["app.py"]
    assert found.notices == ["skipped broken.py: it is not valid Python (invalid syntax on line 1)"]
    assert labels(found) == ["a"]


# --------------------------------------------------------------- module names


def test_a_module_name_is_found_on_disk_and_nothing_is_imported(
    shop: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    write(shop, "shop/agents/reviewer.py", "raise SystemExit('imported!')\n" + REVIEWER)
    monkeypatch.chdir(shop)
    before = set(sys.modules)
    found = analyze("shop.agents.writer")
    assert found.title == "shop.agents.writer"
    # Named from the directory the module was found in, as its dotted name spells it.
    assert found.files[0] == "shop/agents/writer.py"
    assert [(b.name, b.location and b.location.file) for b in found.agents] == [
        ("writer", "shop/agents/writer.py"),
        ("reviewer", "shop/agents/reviewer.py"),
    ]
    assert [b.name for b in analyze("shop.agents.writer:writer").agents] == ["writer", "reviewer"]
    assert [b.name for b in analyze("shop.agents.reviewer:reviewer").agents] == ["reviewer"]
    # A package is read like a directory; with a name, the name is looked up in its __init__.py.
    assert [b.name for b in analyze("shop").agents] == ["reviewer", "writer"]
    assert analyze("shop.agents").files[:3] == [
        "shop/agents/__init__.py",
        "shop/agents/reviewer.py",
        "shop/agents/writer.py",
    ]
    assert [b.name for b in analyze("shop.agents:reviewer").agents] == ["reviewer"]
    assert not {name for name in set(sys.modules) - before if name.partition(".")[0] == "shop"}

    with pytest.raises(TargetError, match="no agent or graph is assigned to 'missing'"):
        analyze("shop.agents:missing")
    with pytest.raises(TargetError, match=r"no module named 'shop\.nothing'"):
        analyze("shop.nothing")


def test_a_module_name_is_also_looked_for_in_src_and_on_sys_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    one_agent(work, "src/inner/agents.py", "in_src")
    one_agent(work, "loose/agents.py", "namespace")  # a directory without __init__.py
    one_agent(tmp_path, "elsewhere/outer/agents.py", "on_path")
    write(tmp_path, "elsewhere/outer/__init__.py")
    monkeypatch.chdir(work)
    monkeypatch.syspath_prepend(str(tmp_path / "elsewhere"))
    assert labels(analyze("inner.agents")) == ["in_src"]
    assert analyze("inner.agents").files == ["inner/agents.py"]
    assert labels(analyze("loose.agents")) == ["namespace"]
    assert labels(analyze("loose")) == ["namespace"]
    assert labels(analyze("outer.agents")) == ["on_path"]
    assert labels(analyze("outer")) == ["on_path"]
    with pytest.raises(TargetError, match="no module named 'nonexistent_pkg'"):
        analyze("nonexistent_pkg:agent")
    with pytest.raises(TargetError, match=r"loose is a directory without an __init__\.py, so it defines no names"):
        analyze("loose:agent")


def test_for_an_installed_module_nothing_outside_its_own_package_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "env" / "lib" / "site-packages"
    write(site, "vendor_agents/__init__.py", "from .core import assistant\n")
    write(
        site,
        "vendor_agents/core.py",
        """
        from pydantic_ai import Agent
        from vendor_agents.prompts import PROMPT
        from neighbour import MODEL

        assistant = Agent(MODEL, name="assistant", instructions=PROMPT)
        """,
    )
    write(site, "vendor_agents/prompts.py", "PROMPT = 'Assist.'\n")
    write(site, "neighbour/__init__.py", "MODEL = 'openai:gpt-5.2'\n")
    one_agent(site, "single_module.py", "single")
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(site))
    found = analyze("vendor_agents")
    assert found.files == ["vendor_agents/__init__.py", "vendor_agents/core.py", "vendor_agents/prompts.py"]
    (assistant,) = found.agents
    assert [i.text for i in assistant.instructions] == ["Assist."]  # its own package is followed
    assert assistant.model is not None
    assert assistant.model.expression == "MODEL"  # another installed package is not
    assert analyze("vendor_agents.core").files == ["vendor_agents/core.py", "vendor_agents/prompts.py"]
    assert labels(analyze("single_module")) == ["single"]
