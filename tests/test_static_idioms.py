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
