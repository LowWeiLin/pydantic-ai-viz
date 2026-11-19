"""Ways of writing agents that the real-world set (tests/realworld) showed to be common, and what is read of them."""

from __future__ import annotations

import textwrap
from pathlib import Path

from static_support import agent, analyze_source, one_agent, properties, tool

from pydantic_ai_viz import analyze

# ------------------------------------------------- the model, from a setting


def test_a_model_read_from_the_environment_is_shown_with_the_default_the_source_gives(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        import os
        from os import environ, getenv
        from pydantic_ai import Agent
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        MODEL = os.getenv("PYDANTIC_AI_MODEL", "openai:gpt-5.2")
        by_default = Agent(MODEL)
        inline = Agent(os.environ.get("MODEL", "gpt-5-mini"))
        with_or = Agent(getenv("MODEL") or "anthropic:claude-sonnet-5")
        keyword = Agent(model=os.getenv("MODEL", default="test"))
        required = Agent(environ["MODEL"])
        optional = Agent(os.getenv("MODEL"))
        computed_default = Agent(os.getenv("MODEL", pick_default()))
        other_function = Agent(config.get("MODEL", "openai:gpt-5.2"))


        def get_model():
            llm = os.getenv("MODEL_CHOICE", "gpt-4.1-mini")
            base_url = os.getenv("BASE_URL", "https://api.openai.com/v1")
            return OpenAIChatModel(llm, provider=OpenAIProvider(base_url=base_url))


        through_class = Agent(get_model())
        class_without_default = Agent(OpenAIChatModel(os.environ["LLM"]))
        """,
    )

    def model(label: str) -> tuple[str | None, str | None, str | None, bool, str | None]:
        info = agent(found, label).model
        assert info is not None
        return info.id, info.provider, info.name, info.offline, info.expression

    # Never an id: which model runs is decided by the environment. The default is what the source says.
    assert model("by_default") == (None, "openai", "gpt-5.2", False, 'os.getenv("PYDANTIC_AI_MODEL", "openai:gpt-5.2")')
    assert agent(found, "by_default").warnings == [
        "the model is read from the environment variable `PYDANTIC_AI_MODEL` when the code runs; "
        "`openai:gpt-5.2` is the default the source gives"
    ]
    assert model("inline") == (None, None, "gpt-5-mini", False, 'os.environ.get("MODEL", "gpt-5-mini")')
    assert model("with_or")[:3] == (None, "anthropic", "claude-sonnet-5")
    assert model("keyword")[:4] == (None, None, "test", False)
    for label in ("required", "optional"):
        assert model(label)[:3] == (None, None, agent(found, label).model.expression)  # type: ignore[union-attr]
        assert agent(found, label).warnings == [
            "the model is read from the environment variable `MODEL`, so it is only known when the code runs"
        ]
    for label in ("computed_default", "other_function"):
        assert agent(found, label).warnings[0].startswith("the model is set by `")

    assert model("through_class") == (None, None, "gpt-4.1-mini", False, "get_model()")
    assert agent(found, "through_class").warnings == [
        "the model name is read from the environment variable `MODEL_CHOICE` when the code runs; "
        "`gpt-4.1-mini` is the default the source gives"
    ]
    assert model("class_without_default")[2] == 'OpenAIChatModel(os.environ["LLM"])'
    assert agent(found, "class_without_default").warnings == [
        "the model is read from the environment variable `LLM`, so it is only known when the code runs"
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
    assert (from_environment.id, from_environment.provider, from_environment.name) == (None, "openai", "gpt-5.2")
    assert agent(found, "from_environment").warnings == [
        "the model is read from the environment variable `PYDANTIC_AI_MODEL` when the code runs; "
        "`openai:gpt-5.2` is the default the source gives"
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
    assert found.warnings == [
        "one of the system prompts is the expression `load_prompt().format(date=today())`; its text is only "
        "known at run time"
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


# ------------------------------------------------------- tools that are not defs


def test_a_tool_made_by_a_library_function_is_shown_under_the_name_the_library_gives_it(tmp_path: Path) -> None:
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
    assert [(t.name, t.function, t.parameters_schema, t.location and t.location.line) for t in found.tools] == [
        ("duckduckgo_search", "pydantic_ai.common_tools.duckduckgo.duckduckgo_search_tool", None, 10),
        ("tavily_search", "pydantic_ai.common_tools.tavily.tavily_search_tool", None, 10),
    ]
    assert found.warnings == [
        "a tool is built by `their_search_tool()`; it cannot be described without running the code",
        "the tool `duckduckgo_search` is `pydantic_ai.common_tools.duckduckgo.duckduckgo_search_tool`, which is "
        "defined outside the analysed source, so its description and parameters are not shown",
        "the tool `tavily_search` is `pydantic_ai.common_tools.tavily.tavily_search_tool`, which is defined "
        "outside the analysed source, so its description and parameters are not shown",
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
    assert toolsets("elsewhere") == [("FunctionToolset", "function", ["TheirToolset"], False)]
    for label in ("by_keyword", "own_class", "elsewhere"):
        assert agent(found, label).warnings == []
    # A class that is no toolset, and a toolset whose inside is computed, stay what they are: opaque.
    assert toolsets("not_a_toolset") == [("Registry", "other", [], True)]
    assert toolsets("nothing_known_inside") == [("CodeExecutionToolset", "other", [], True)]
    assert agent(found, "not_a_toolset").tools == agent(found, "nothing_known_inside").tools == []
