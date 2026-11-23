"""Ways of writing agents that the real-world set (tests/realworld) showed to be common, and what is read of them."""

from __future__ import annotations

import textwrap
from pathlib import Path

from static_support import agent, analyze_source, live_agents, one_agent, properties, tool

from pydantic_ai_viz import AgentBlueprint, analyze
from pydantic_ai_viz._compat import pydantic_ai_version

# ------------------------------------------------- the model, from a setting


def test_a_model_read_at_run_time_is_its_expression_with_one_message_whatever_reads_it(tmp_path: Path) -> None:
    # A config object, a function, an environment variable read in a way that is not one of the few the
    # analyser knows (test_static_stdlib.py): all are outside the source, so none is singled out, and no
    # default the source passes along is taken for the value.
    found = analyze_source(
        tmp_path,
        """
        import os
        from os import getenv
        from pydantic_ai import Agent
        from pydantic_ai.models.openai import OpenAIChatModel

        with_or = Agent(getenv("MODEL") or "anthropic:claude-sonnet-5")
        computed_default = Agent(os.getenv("MODEL", pick_default()))
        computed_name = Agent(os.getenv(NAME_OF_THE_VARIABLE, "openai:gpt-5.2"))
        other_function = Agent(config.get("MODEL", "openai:gpt-5.2"))
        other_mapping = Agent(settings["MODEL"])


        def get_model():
            return OpenAIChatModel(load_model_name())


        through_class = Agent(get_model())
        """,
    )

    def model(label: str) -> tuple[str | None, str | None, str | None, bool, str | None]:
        info = agent(found, label).model
        assert info is not None
        assert info.env is None
        return info.id, info.provider, info.name, info.offline, info.expression

    expressions = {
        "with_or": 'getenv("MODEL") or "anthropic:claude-sonnet-5"',
        "computed_default": 'os.getenv("MODEL", pick_default())',
        "computed_name": 'os.getenv(NAME_OF_THE_VARIABLE, "openai:gpt-5.2")',
        "other_function": 'config.get("MODEL", "openai:gpt-5.2")',
        "other_mapping": 'settings["MODEL"]',
        "through_class": "get_model()",
    }
    for label, expression in expressions.items():
        assert model(label) == (None, None, expression, False, expression)
        assert agent(found, label).warnings == [
            f"the model is set by `{expression}`, so it is only known when the code runs"
        ]


def test_a_model_held_in_a_variable_is_shown_as_what_the_variable_was_assigned(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent

        model = pick_model(settings)
        named = Agent(model)
        called = Agent(pick_model(settings))
        """,
    )
    for label in ("named", "called"):
        info = agent(found, label).model
        assert info is not None
        assert (info.id, info.name, info.expression) == (None, "pick_model(settings)", "pick_model(settings)")
        assert agent(found, label).warnings == [
            "the model is set by `pick_model(settings)`, so it is only known when the code runs"
        ]


def test_a_cast_is_looked_through_to_the_value_it_wraps(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        import os
        import typing
        from typing import cast
        from pydantic_ai import Agent
        from pydantic_ai.models import KnownModelName

        literal = Agent(cast(KnownModelName, "openai:gpt-5.2"), retries=typing.cast(int, 3))
        from_environment = Agent(cast(KnownModelName, os.getenv("PYDANTIC_AI_MODEL", "openai:gpt-5.2")))
        another_cast = Agent(numpy.cast(KnownModelName, "openai:gpt-5.2"))
        """,
    )
    literal = agent(found, "literal")
    assert literal.model is not None
    assert (literal.model.id, literal.model.expression, literal.settings.tool_retries) == ("openai:gpt-5.2", None, 3)
    assert literal.warnings == []
    from_environment = agent(found, "from_environment").model
    assert from_environment is not None
    assert (from_environment.id, from_environment.provider) == (None, None)
    assert from_environment.env is not None
    assert (from_environment.env.variable, from_environment.env.default) == ("PYDANTIC_AI_MODEL", "openai:gpt-5.2")
    assert agent(found, "from_environment").warnings == [
        "the model comes from the environment variable `PYDANTIC_AI_MODEL` when the code runs; "
        "the source's default is `openai:gpt-5.2`"
    ]
    # Only the cast of the typing module is known to hand its argument back.
    assert agent(found, "another_cast").warnings == [
        'the model is set by `numpy.cast(KnownModelName, "openai:gpt-5.2")`, so it is only known when the code runs'
    ]


def test_a_model_written_as_an_f_string_gives_the_provider_it_spells_out(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent

        provider_given = Agent(f"openai:{settings.model}")
        offline = Agent(f"test:{settings.model}")
        provider_computed = Agent(f"{settings.vendor}:{settings.model}")
        no_provider = Agent(f"gpt-{settings.version}")
        NAME = f"anthropic:claude-{settings.tier}-5"
        through_a_name = Agent(NAME)
        """,
    )

    def model(label: str) -> tuple[str | None, str | None, str | None, bool, str | None]:
        info = agent(found, label).model
        assert info is not None
        return info.id, info.provider, info.name, info.offline, info.expression

    assert model("provider_given") == (None, "openai", "{settings.model}", False, 'f"openai:{settings.model}"')
    assert agent(found, "provider_given").warnings == [
        'the model name is computed by `f"openai:{settings.model}"`, so it is only known when the code runs; '
        "the provider `openai` is what the source gives"
    ]
    assert model("offline")[:4] == (None, "test", "{settings.model}", True)
    assert model("through_a_name")[:3] == (None, "anthropic", "claude-{settings.tier}-5")
    for label in ("provider_computed", "no_provider"):
        expression = model(label)[4]
        assert model(label) == (None, None, expression, False, expression)
        assert agent(found, label).warnings == [
            f"the model is set by `{expression}`, so it is only known when the code runs"
        ]


def test_a_prompt_filled_in_with_format_is_shown_as_its_template(tmp_path: Path) -> None:
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent

        PROMPT = \"\"\"You are a researcher.
        Today is {date}.\"\"\"

        researcher = Agent(
            "test",
            instructions=PROMPT.format(date=today()),
            system_prompt=["Call the user {name}.".format(name=name), load_prompt().format(date=today())],
        )
        """,
    )
    assert [(i.kind, i.dynamic, i.text, i.expression) for i in found.instructions] == [
        ("instructions", True, "You are a researcher.\nToday is {date}.", None),
        ("system_prompt", True, "Call the user {name}.", None),
        ("system_prompt", True, None, "load_prompt().format(date=today())"),
    ]
    # The templates are shown whole, so only the prompt whose template is not in the source is a warning.
    assert found.warnings == [
        "one of the system prompts is the expression `load_prompt().format(date=today())`; its text is only "
        "known when the code runs",
    ]


# ------------------------------------------------------ agents that are wrapped


def test_running_a_wrapper_around_an_agent_is_delegation_to_that_agent(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent, RunContext
        from pydantic_ai.agent import WrapperAgent
        from pydantic_ai.durable_exec.dbos import DBOSAgent
        from pydantic_ai.durable_exec.temporal import TemporalAgent
        from somewhere_else import TheirAgent

        answerer = Agent("test", name="answerer")
        temporal_answerer = TemporalAgent(answerer)
        twice_wrapped = WrapperAgent(wrapped=DBOSAgent(answerer))
        not_ours = TheirAgent(answerer)

        questioner = Agent("test", name="questioner")


        @questioner.tool
        async def ask(ctx: RunContext[None], question: str) -> str:
            return (await temporal_answerer.run(question)).output


        @questioner.tool_plain
        async def ask_again(question: str) -> str:
            return (await twice_wrapped.run(question)).output


        @questioner.tool_plain
        async def ask_them(question: str) -> str:
            return (await not_ours.run(question)).output


        temporal_questioner = TemporalAgent(questioner)
        """,
    )
    answerer, questioner = found.agents
    assert [(d.tool_name, d.agent_id) for d in questioner.delegates] == [
        ("ask", answerer.id),
        ("ask_again", answerer.id),
    ]
    assert answerer.delegates == []
    # A wrapper is not an agent of its own, and can be named to select the agent it wraps.
    assert len(found.agents) == 2
    selected = analyze(f"{tmp_path / 'module.py'}:temporal_questioner")
    assert [b.name for b in selected.agents] == ["questioner", "answerer"]


def test_running_an_agent_picked_from_a_written_out_dict_or_list_is_delegation_to_each_it_may_be(
    tmp_path: Path,
) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent, RunContext

        gp = Agent("test", name="gp")
        cardiology = Agent("test", name="cardiology")
        neurology = Agent("test", name="neurology")
        SPECIALISTS = {"general": gp, "cardiology": cardiology, "neurology": neurology, "other": "not an agent"}
        IN_ORDER = [gp, neurology]

        triage = Agent("test", name="triage")


        @triage.tool
        async def consult(ctx: RunContext[None], specialty: str, question: str) -> str:
            specialist = SPECIALISTS.get(specialty)
            if specialist is None:
                return "no such specialist"
            return (await specialist.run(question)).output


        @triage.tool_plain
        async def consult_cardiology(question: str) -> str:
            return (await SPECIALISTS["cardiology"].run(question)).output


        @triage.tool_plain
        def consult_first(index: int, question: str) -> str:
            return IN_ORDER[index].run_sync(question).output


        @triage.tool_plain
        def look_up(specialty: str) -> str:
            return SPECIALISTS.get(specialty).name
        """,
    )
    gp, cardiology, neurology, triage = found.agents
    assert [(d.tool_name, d.agent_id) for d in triage.delegates] == [
        ("consult", gp.id),
        ("consult", cardiology.id),
        ("consult", neurology.id),
        ("consult_cardiology", cardiology.id),
        ("consult_first", gp.id),
        ("consult_first", neurology.id),
    ]
    assert gp.delegates == cardiology.delegates == neurology.delegates == []


def test_running_the_agent_a_run_is_given_as_its_dependencies_is_delegation_to_that_agent(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from dataclasses import dataclass

        from pydantic_ai import Agent, RunContext
        from pydantic_ai.agent import AbstractAgent

        search = Agent("test", name="search")
        summarise = Agent("test", name="summarise")
        unused = Agent("test", name="unused")


        @dataclass
        class Deps:
            searcher: AbstractAgent
            limit: int = 3


        analysis = Agent("test", name="analysis", deps_type=AbstractAgent)


        @analysis.tool
        async def extra_search(ctx: RunContext[AbstractAgent], query: str) -> str:
            return (await ctx.deps.run(query)).output


        @analysis.tool_plain
        def not_a_context(deps: AbstractAgent, query: str) -> str:
            return deps.run_sync(query).output


        report = Agent("test", name="report", deps_type=Deps)


        @report.tool
        async def look_further(ctx: RunContext[Deps], query: str) -> str:
            return (await ctx.deps.searcher.run(query)).output


        async def main() -> None:
            await analysis.run("first", deps=search)
            await analysis.run("again", deps=summarise)
            await report.run("report", deps=Deps(searcher=search, limit=5))
            await report.run("report", deps=Deps(search))
        """,
    )
    search, summarise, _, analysis, report = found.agents
    # Each agent that a run in the source hands over as the dependencies; a field of them is followed by keyword.
    assert [(d.tool_name, d.agent_id) for d in analysis.delegates] == [
        ("extra_search", search.id),
        ("extra_search", summarise.id),
    ]
    assert [(d.tool_name, d.agent_id) for d in report.delegates] == [("look_further", search.id)]


# ------------------------------------------------------- tools that are not defs


def test_a_tool_made_by_a_function_of_pydantic_ai_is_read_from_its_installed_source(tmp_path: Path) -> None:
    # More in test_static_installed.py.
    found = one_agent(
        tmp_path,
        """
        import os
        from pydantic_ai import Agent
        from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool
        from pydantic_ai.common_tools.tavily import tavily_search_tool
        from other_tools import their_search_tool

        search = Agent(
            "test",
            tools=[duckduckgo_search_tool(), tavily_search_tool(os.environ["KEY"]), their_search_tool()],
        )
        """,
    )
    version = pydantic_ai_version()
    assert [(t.name, t.function, t.library_version, t.location and t.location.line) for t in found.tools] == [
        ("duckduckgo_search", "pydantic_ai.common_tools.duckduckgo.DuckDuckGoSearchTool.__call__", version, 10),
        ("tavily_search", "pydantic_ai.common_tools.tavily.tavily_search_tool", version, 10),
    ]
    assert found.tools[0].parameters_schema == {
        "additionalProperties": False,
        "properties": {"query": {"description": "The query to search for.", "type": "string"}},
        "required": ["query"],
        "type": "object",
    }
    assert found.warnings == [
        "a tool is built by `their_search_tool()`; it cannot be described without running the code",
        "the tool `tavily_search` is made by `pydantic_ai.common_tools.tavily.tavily_search_tool`; its name and "
        f"description are read from the installed Pydantic AI {version}, and its parameters are only known when "
        "the code runs",
    ]


def test_a_method_of_an_object_made_in_the_source_is_a_tool_without_its_self(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "database.py").write_text(
        textwrap.dedent(
            '''
            from pydantic_ai import RunContext


            class Database:
                def __init__(self, path: str = ":memory:") -> None:
                    self.path = path

                def query(self, sql: str, limit: int = 10) -> list[dict]:
                    """Run a query.

                    Args:
                        sql: The statement.
                        limit: At most this many rows.
                    """
                    return []

                async def insert_rows(self, ctx: RunContext[None], table: str, rows: list[dict]) -> int:
                    """Insert rows."""
                    return 0

                tables = ["a"]
            '''
        ),
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        textwrap.dedent(
            """
            from pydantic_ai import Agent
            from database import Database

            db = Database()
            agent = Agent("test", tools=[db.query, db.insert_rows, db.tables, db.missing, unknown.method])
            """
        ),
        encoding="utf-8",
    )
    (found,) = analyze(tmp_path / "app.py").agents
    query = tool(found, "query")
    assert (query.function, query.takes_ctx, query.is_async, query.description) == (
        "database.Database.query",
        False,
        False,
        "Run a query.",
    )
    assert properties(query.parameters_schema) == {
        "sql": {"description": "The statement.", "type": "string"},
        "limit": {"default": 10, "description": "At most this many rows.", "type": "integer"},
    }
    insert = tool(found, "insert_rows")
    assert (insert.takes_ctx, insert.is_async) == (True, True)
    assert list(properties(insert.parameters_schema)) == ["table", "rows"]
    assert query.location is not None
    assert (query.location.file, query.location.line) == ("database.py", 9)
    # An attribute that is not a method, one that does not exist, one of something unknown: said, not guessed.
    assert found.warnings == [
        "a tool is built by `db.tables`; it cannot be described without running the code",
        "a tool is built by `db.missing`; it cannot be described without running the code",
        "a tool is built by `unknown.method`; it cannot be described without running the code",
    ]


# ------------------------------------------------- tools registered in a loop


LOOPS = """
from pydantic_ai import Agent
from pydantic_ai.toolsets import FunctionToolset


def search(query: str) -> str:
    \"\"\"Search the web.\"\"\"
    return query


def fetch(url: str) -> str:
    return url


def summarise(text: str) -> str:
    return text


written_out = Agent("test")
for function in (search, fetch):
    written_out.tool_plain(function)

TOOLS = [search, summarise]
named = Agent("test")
for function in TOOLS:
    named.tool_plain(function, retries=2)

twice = Agent("test")
for function in (search, fetch):
    for attempt in (1, 2):
        twice.tool_plain(function)

toolset = FunctionToolset()
for function in [fetch, summarise]:
    toolset.add_function(function)
through_a_toolset = Agent("test", toolsets=[toolset])

computed = Agent("test")
for function in load_tools():
    computed.tool_plain(function)

GROWN = [search]
GROWN.append(fetch)
grown = Agent("test")
for function in GROWN:
    grown.tool_plain(function)

paired = Agent("test")
for name, function in registry.items():
    paired.tool_plain(function)

unknown_toolset = FunctionToolset()
unknown_toolset.add_function(make_tool())
unknown_toolset.add_tool(make_tool())
unknown_toolset.add_function(search)
partly = Agent("test", toolsets=[unknown_toolset, FunctionToolset([fetch, make_tool()])])
"""


def test_a_tool_registered_in_a_loop_over_a_written_out_list_is_one_tool_per_item(tmp_path: Path) -> None:
    found = analyze_source(tmp_path, LOOPS)

    def tools(label: str) -> list[str]:
        return [item.name for item in agent(found, label).tools]

    assert tools("written_out") == ["search", "fetch"]
    assert tool(agent(found, "written_out"), "search").description == "Search the web."
    assert tools("named") == ["search", "summarise"]
    assert [item.max_retries for item in agent(found, "named").tools] == [2, 2]
    # Only a loop whose variable the registration names multiplies it.
    assert tools("twice") == ["search", "fetch"]
    assert tools("through_a_toolset") == ["fetch", "summarise"]
    for label in ("written_out", "named", "twice", "through_a_toolset"):
        assert agent(found, label).warnings == []


def test_a_tool_whose_function_cannot_be_traced_is_said_to_be_there(tmp_path: Path) -> None:
    found = analyze_source(tmp_path, LOOPS)
    unread = "which function that is is only known when the code runs"
    # A loop over something computed, over a list that is added to afterwards, and one that unpacks pairs.
    for label, call in (
        ("computed", "computed.tool_plain(function)"),
        ("grown", "grown.tool_plain(function)"),
        ("paired", "paired.tool_plain(function)"),
    ):
        assert agent(found, label).tools == []
        assert agent(found, label).warnings == [f"a tool is added by `{call}`; {unread}"]
    # A toolset says so on every agent that uses it, and keeps the tools that can be read.
    partly = agent(found, "partly")
    assert [item.name for item in partly.tools] == ["search", "fetch"]
    assert partly.warnings == [
        f"a tool is added by `unknown_toolset.add_function(make_tool())`; {unread}",
        f"a tool is added by `unknown_toolset.add_tool(make_tool())`; {unread}",
        "a tool is built by `make_tool()`; it cannot be described without running the code",
    ]


# ------------------------------------------------- toolsets that wrap toolsets


def test_a_toolset_class_that_is_handed_a_toolset_is_a_wrapper_around_it(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.toolsets import FunctionToolset, WrapperToolset
        from pydantic_ai.toolsets.code_execution import CodeExecutionToolset
        from somewhere import TheirToolset

        tools = FunctionToolset()


        @tools.tool_plain
        def add(a: int, b: int) -> int:
            return a + b


        class Logged(WrapperToolset):
            pass


        class Registry:
            def __init__(self, toolset): ...


        by_keyword = Agent("test", toolsets=[CodeExecutionToolset(toolset=tools)])
        own_class = Agent("test", toolsets=[Logged(tools.prefixed("math"))])
        elsewhere = Agent("test", toolsets=[TheirToolset(wrapped=tools, limit=3)])
        not_a_toolset = Agent("test", toolsets=[Registry(toolset=tools)])
        nothing_known_inside = Agent("test", toolsets=[CodeExecutionToolset(toolset=make_tools())])
        """,
    )

    def toolsets(label: str) -> list[tuple[str, str, list[str], bool]]:
        return [(t.label, t.kind, t.wrappers, t.opaque) for t in agent(found, label).toolsets]

    assert toolsets("by_keyword") == [("FunctionToolset", "function", ["CodeExecutionToolset"], False)]
    assert [t.name for t in agent(found, "by_keyword").tools] == ["add"]
    assert toolsets("own_class") == [("FunctionToolset", "function", ["Logged", "PrefixedToolset"], False)]
    assert [t.name for t in agent(found, "own_class").tools] == ["math_add"]
    for label in ("by_keyword", "own_class"):
        assert agent(found, label).warnings == []
    # Another library's class is not taken for a wrapper because of its name: what it does is not in the source.
    assert toolsets("elsewhere") == [("TheirToolset(wrapped=tools, limit=3)", "other", [], True)]
    assert agent(found, "elsewhere").warnings == [
        "a toolset is built by `TheirToolset(wrapped=tools, limit=3)`; its tools are only known when the code runs"
    ]
    # A class that is no toolset, and a toolset whose inside is computed, stay what they are: opaque.
    assert toolsets("not_a_toolset") == [("Registry", "other", [], True)]
    assert toolsets("nothing_known_inside") == [("CodeExecutionToolset", "other", [], True)]
    assert agent(found, "not_a_toolset").tools == agent(found, "nothing_known_inside").tools == []


def test_a_class_derived_from_function_toolset_has_the_tools_its_methods_register(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "checkpoints.py").write_text(
        textwrap.dedent(
            '''
            from typing import Any

            from pydantic_ai import RunContext
            from pydantic_ai.toolsets import FunctionToolset


            def helper(x: int) -> int:
                """Help."""
                return x


            class CheckpointToolset(FunctionToolset[Any]):
                def __init__(self, *, store=None, id: str = "checkpoints") -> None:
                    super().__init__(id=id)

                    @self.tool(description="Save a checkpoint.")
                    async def save_checkpoint(ctx: RunContext[Any], label: str) -> str:
                        return label

                    @self.tool_plain
                    def list_checkpoints() -> list[str]:
                        """List them."""
                        return []

                    self.add_function(helper)
                    self.add_function(make_tool())


            class Unrelated:
                def __init__(self) -> None:
                    @self.tool
                    def not_a_tool(x: int) -> int:
                        return x
            '''
        ),
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text(
        textwrap.dedent(
            """
            from pydantic_ai import Agent
            from checkpoints import CheckpointToolset, Unrelated

            checkpoints = CheckpointToolset(store=None)
            agent = Agent("test", toolsets=[checkpoints, CheckpointToolset(id="second").prefixed("cp"), Unrelated()])
            """
        ),
        encoding="utf-8",
    )
    (found,) = analyze(tmp_path / "app.py").agents
    assert [(t.label, t.kind, t.toolset_id, t.wrappers, t.opaque) for t in found.toolsets] == [
        ("CheckpointToolset", "function", None, [], False),
        ("CheckpointToolset 'second'", "function", "second", ["PrefixedToolset"], False),
        ("Unrelated", "other", None, [], True),
    ]
    assert [(t.name, t.toolset, t.takes_ctx) for t in found.tools] == [
        ("save_checkpoint", "ts0", True),
        ("list_checkpoints", "ts0", False),
        ("helper", "ts0", False),
        ("cp_save_checkpoint", "ts1", True),
        ("cp_list_checkpoints", "ts1", False),
        ("cp_helper", "ts1", False),
    ]
    save = tool(found, "save_checkpoint")
    assert (save.description, save.function, save.is_async) == (
        "Save a checkpoint.",
        "checkpoints.CheckpointToolset.__init__.<locals>.save_checkpoint",
        True,
    )
    assert list(properties(save.parameters_schema)) == ["label"]
    assert save.location is not None
    assert (save.location.file, save.location.line) == ("checkpoints.py", 18)
    assert found.warnings == [
        "a tool is added by `self.add_function(make_tool())`; which function that is is only known when the code runs"
    ]


def test_a_capability_of_the_project_gives_the_agent_the_toolset_its_get_toolset_returns(tmp_path: Path) -> None:
    source = '''
        from dataclasses import dataclass
        from typing import Any

        from pydantic_ai import Agent
        from pydantic_ai.capabilities import AbstractCapability, WebSearch
        from pydantic_ai.toolsets import FunctionToolset


        def search(query: str) -> str:
            """Search the web."""
            return query


        class MemoryToolset(FunctionToolset[Any]):
            def __init__(self) -> None:
                super().__init__(id="memory")

                @self.tool_plain
                def read_memory() -> str:
                    return ""


        @dataclass
        class MemoryCapability(AbstractCapability[Any]):
            def __post_init__(self) -> None:
                self._toolset = MemoryToolset()

            def get_toolset(self):
                return self._toolset


        class SearchCapability(AbstractCapability[Any]):
            def get_toolset(self):
                if not self.enabled:
                    return None
                return FunctionToolset([search]).prefixed("web")


        class LoudSearch(SearchCapability):
            pass


        class Computed(AbstractCapability[Any]):
            def get_toolset(self):
                return self.build()


        class NoTools(AbstractCapability[Any]):
            def get_instructions(self):
                return "Be brief."


        memory = MemoryCapability()
        agent = Agent(
            "test",
            capabilities=[memory, LoudSearch(), Computed(), NoTools(), WebSearch()],
        )
        '''
    found = one_agent(tmp_path, source)
    # As Pydantic AI holds it: inside a CapabilityOwnedToolset, after the toolsets the agent is given.
    # (The id that ``MemoryToolset`` hands to ``super().__init__`` is not read, as for any derived toolset class.)
    assert [(t.label, t.kind, t.toolset_id, t.wrappers, t.opaque) for t in found.toolsets] == [
        ("MemoryToolset", "function", None, ["CapabilityOwnedToolset"], False),
        ("FunctionToolset", "function", None, ["CapabilityOwnedToolset", "PrefixedToolset"], False),
        ("self.build()", "other", None, ["CapabilityOwnedToolset"], True),
    ]
    assert [(t.name, t.toolset) for t in found.tools] == [("read_memory", "ts0"), ("web_search", "ts1")]
    assert tool(found, "web_search").description == "Search the web."
    assert [c.name for c in found.capabilities] == [
        "MemoryCapability",
        "LoudSearch",
        "Computed",
        "NoTools",
        "WebSearch",
    ]
    assert [c.toolsets for c in found.capabilities] == [["ts0"], ["ts1"], ["ts2"], [], []]
    assert found.warnings == ["a toolset is built by `self.build()`; its tools are only known when the code runs"]
    # A toolset that a capability gives is not one that no agent is given.
    analysed = analyze_source(tmp_path, source)
    assert analysed.notices == []


def test_the_toolsets_pydantic_ai_capabilities_give_are_read_as_a_live_agent_shows_them(tmp_path: Path) -> None:
    source = '''
        from pydantic_ai import Agent
        from pydantic_ai.capabilities import Capability, Toolset
        from pydantic_ai.toolsets import FunctionToolset


        def lookup(topic: str) -> str:
            """Look a topic up."""
            return topic


        def define(word: str) -> str:
            """Define a word."""
            return word


        agent = Agent(
            "test",
            capabilities=[
                Toolset(FunctionToolset([lookup], id="kb")),
                Capability(tools=[define], id="mine"),
                Capability(tools=[define], toolsets=[FunctionToolset([lookup], id="more")]),
                Capability(toolsets=[FunctionToolset([lookup])]),
            ],
        )
        '''
    found = one_agent(tmp_path, source)
    (live,) = live_agents(tmp_path / "module.py").values()

    def shape(blueprint: AgentBlueprint) -> list[tuple[str, str, str | None, list[str]]]:
        return [(t.label, t.kind, t.toolset_id, t.wrappers) for t in blueprint.toolsets]

    assert shape(found) == shape(live)
    assert [(t.name, t.toolset) for t in found.tools] == [(t.name, t.toolset) for t in live.tools]
    assert [c.toolsets for c in found.capabilities] == [c.toolsets for c in live.capabilities[2:]]
    assert [c.toolsets for c in found.capabilities] == [["ts0"], ["ts1"], ["ts2", "ts3"], ["ts4"]]
    assert [t.description for t in found.tools if t.name == "define"] == ["Define a word."] * 2
    assert found.warnings == []


def test_a_toolset_or_capability_held_in_a_variable_is_named_by_what_it_was_assigned(tmp_path: Path) -> None:
    found = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent

        todo_toolset = create_todo_toolset(storage=None, id="todo")
        workspace = make_workspace() if shared else None
        agent = Agent("test", toolsets=[todo_toolset], capabilities=[workspace])
        """,
    )
    (toolset,) = found.toolsets
    assert (toolset.label, toolset.expression, toolset.opaque) == (
        'create_todo_toolset(storage=None, id="todo")',
        'create_todo_toolset(storage=None, id="todo")',
        True,
    )
    assert found.warnings == [
        'a toolset is built by `create_todo_toolset(storage=None, id="todo")`; its tools are only known when the '
        "code runs",
        "a capability is built by `make_workspace() if shared else None`; its type is only known when the code runs",
    ]


# ------------------------------------- tools registered by a function that is handed the agent


HANDED = """
from mcp.server.fastmcp import FastMCP
from pydantic_ai import Agent, RunContext
from pydantic_ai.toolsets import FunctionToolset


def register_files(agent):
    @agent.tool
    def list_files(ctx: RunContext, directory: str = ".") -> list[str]:
        \"\"\"List files.\"\"\"
        return []

    @agent.tool_plain(retries=2)
    def read_file(path: str) -> str:
        return path


def register_search(agent: Agent, flavour: str = "web"):
    @agent.tool
    def search(ctx, query: str) -> str:
        return query


def register_everything(target):
    register_files(target)
    register_search(agent=target)


def fill(toolset: FunctionToolset):
    @toolset.tool_plain
    def in_toolset(x: int) -> int:
        return x


def register_on_server(server):
    @server.tool
    def not_a_pydantic_ai_tool(x: int) -> int:
        return x


coder = Agent("test", name="coder")
register_everything(coder)
reader = Agent("test", name="reader")
register_files(reader)
tools = FunctionToolset()
fill(tools)
with_toolset = Agent("test", name="with_toolset", toolsets=[tools])
register_on_server(FastMCP("files"))
"""


def test_a_tool_registered_by_a_function_goes_to_the_agent_the_function_is_called_with(tmp_path: Path) -> None:
    found = analyze_source(tmp_path, HANDED)

    def tools(label: str) -> list[tuple[str, bool | None, int | None]]:
        return [(item.name, item.takes_ctx, item.max_retries) for item in agent(found, label).tools]

    # Through one function that hands the agent on to two others, by position and by keyword.
    assert tools("coder") == [("list_files", True, None), ("read_file", False, 2), ("search", True, None)]
    assert tools("reader") == [("list_files", True, None), ("read_file", False, 2)]
    assert tools("with_toolset") == [("in_toolset", False, None)]
    listed = tool(agent(found, "coder"), "list_files")
    assert (listed.description, listed.function) == ("List files.", "module.register_files.<locals>.list_files")
    assert list(properties(listed.parameters_schema)) == ["directory"]
    assert [blueprint.warnings for blueprint in found.agents] == [[], [], []]
    # ``@server.tool`` on something that is no agent of Pydantic AI is not counted as a tool that went missing.
    assert found.notices == []


def test_tools_registered_on_an_agent_that_no_call_shows_are_counted_in_a_notice(tmp_path: Path) -> None:
    source = (
        HANDED
        + """

def register_orphan(agent):
    @agent.tool_plain
    def orphan(x: int) -> int:
        return x


REGISTRY = {"orphan": register_orphan}
REGISTRY["orphan"](coder)
"""
    )
    found = analyze_source(tmp_path, source)
    assert "orphan" not in [item.name for blueprint in found.agents for item in blueprint.tools]
    line = source.splitlines().index("    def orphan(x: int) -> int:") + 1
    assert found.notices == [
        "1 tool is registered on an agent or toolset that a function is handed as an argument (`orphan` at "
        f"module.py:{line}); which agent gets it is decided when the code runs, so it is shown on none"
    ]

    # Without any call at all, every such tool is unplaced: the count says how much there is that is not shown.
    uncalled = "\n".join(line for line in HANDED.splitlines() if not line.startswith("register_"))
    uncalled = uncalled.replace("    register_files(target)\n    register_search(agent=target)", "    pass")
    uncalled = uncalled.replace("fill(tools)", "")
    found = analyze_source(tmp_path, uncalled)
    assert [blueprint.tools for blueprint in found.agents] == [[], [], []]
    line = next(number for number, text in enumerate(uncalled.splitlines(), 1) if "def list_files(" in text)
    assert found.notices == [
        "4 tools are registered on an agent or toolset that a function is handed as an argument (for example "
        f"`list_files` at module.py:{line}); which agent gets them is decided when the code runs, so they are "
        "shown on none"
    ]


# --------------------------------------------- an agent made in a base class


def test_what_a_derived_class_adds_to_the_agent_of_its_base_class_is_shown_and_said(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "base.py").write_text(
        textwrap.dedent(
            """
            from pydantic_ai import Agent, RunContext


            class BaseAgent:
                def __init__(self, instructions: str | None = None) -> None:
                    self.agent = Agent("openai:gpt-5.2", instructions=instructions)

                    @self.agent.instructions
                    def add_language(ctx: RunContext[None]) -> str:
                        return "Answer in English."

                async def ask(self, question: str) -> str:
                    return (await self.agent.run(question)).output
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "pdf.py").write_text(
        textwrap.dedent(
            '''
            from pydantic_ai import RunContext

            from base import BaseAgent


            class PDFAgent(BaseAgent):
                def __init__(self) -> None:
                    super().__init__(instructions="Read the PDF.")

                    @self.agent.tool
                    def get_pdf(ctx: RunContext[None], page: int) -> str:
                        """Get one page of the PDF."""
                        return ""

                    @self.agent.instructions
                    def add_path(ctx: RunContext[None]) -> str:
                        return "The file is example.pdf."


            class Unrelated:
                def __init__(self) -> None:
                    @self.agent.tool
                    def not_found(ctx: RunContext[None]) -> str:
                        return ""
            '''
        ),
        encoding="utf-8",
    )
    (found,) = analyze(tmp_path).agents
    assert found.location is not None
    assert (found.label, found.location.file, found.location.line) == ("agent", "base.py", 7)
    assert [(t.name, t.function, t.description) for t in found.tools] == [
        ("get_pdf", "pdf.PDFAgent.__init__.<locals>.get_pdf", "Get one page of the PDF.")
    ]
    assert [i.function for i in found.instructions if i.function] == [
        "base.BaseAgent.__init__.<locals>.add_language",
        "pdf.PDFAgent.__init__.<locals>.add_path",
    ]
    # One construction in the source, one agent per class when the code runs: the difference is said.
    assert found.warnings == [
        "the tool `get_pdf` is added by the class `PDFAgent`, which is derived from `BaseAgent` where this agent is "
        "made; only an agent made through `PDFAgent` has it",
        "the instructions function `add_path` is added by the class `PDFAgent`, which is derived from `BaseAgent` "
        "where this agent is made; only an agent made through `PDFAgent` has it",
        "one of the instructions is the expression `instructions`; its text is only known when the code runs",
    ]


def test_function_toolsets_that_no_agent_is_given_are_counted_in_a_notice(tmp_path: Path) -> None:
    source = """
        from pydantic_ai import Agent
        from pydantic_ai.toolsets import FunctionToolset


        def search(query: str) -> str:
            return query


        used = FunctionToolset([search])
        agent = Agent("test", toolsets=[used.prefixed("web")])


        def create_memory_toolset() -> FunctionToolset:
            toolset = FunctionToolset()

            @toolset.tool_plain
            def read_memory() -> str:
                return ""

            @toolset.tool_plain
            def write_memory(text: str) -> None: ...

            return toolset


        class BrowserToolset(FunctionToolset):
            def __init__(self) -> None:
                super().__init__()

                @self.tool_plain
                def navigate(url: str) -> str:
                    return url


        empty = FunctionToolset()
        """
    found = analyze_source(tmp_path, source)
    assert [t.name for t in agent(found, "agent").tools] == ["web_search"]
    # The one that is used and the one that holds nothing are not counted.
    assert found.notices == [
        "2 function toolsets with 3 tools (for example the one at module.py:15) are given to no agent that the "
        "source shows, so what they hold is shown on none"
    ]

    alone = analyze_source(
        tmp_path,
        """
        from pydantic_ai.toolsets import FunctionToolset

        toolset = FunctionToolset()


        @toolset.tool_plain
        def lookup(key: str) -> str:
            return key
        """,
    )
    assert alone.notices == [
        "1 function toolset with 1 tool (at module.py:4) is given to no agent that the source shows, so what it "
        "holds is shown on none"
    ]
