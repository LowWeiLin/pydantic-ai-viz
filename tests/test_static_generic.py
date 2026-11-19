"""The analyser knows Pydantic AI, pydantic and typing; everything else it reads as plain Python.

What these tests hold the analyser to:

- What a value is comes from one evaluator of the language: names, imports, attributes,
  items of written-out containers, return values of the project's functions and methods,
  f-strings of literals. There is no special case for any other library or for a habit
  of some project.
- What depends on something outside the source (the environment, a file, a call into
  another library or the standard library) is shown as the source text, with the same
  message whatever it is, and nothing it passes along is taken for the value.
"""

from __future__ import annotations

from pathlib import Path

from static_support import agent, analyze_source


def test_values_are_followed_through_the_project_by_the_rules_of_the_language(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent

        PROVIDER = "anthropic"
        MODELS = {"fast": f"{PROVIDER}:claude-haiku-5", "deep": f"{PROVIDER}:claude-opus-5"}
        TIERS = ("fast", "deep")


        class Config:
            model = MODELS[TIERS[1]]
            retries = 3


        class Factory:
            def __init__(self):
                self.config = Config()

            def build(self):
                return Agent(self.config.model, name="built", retries=self.config.retries)


        config = Config()
        folded = Agent(f"{PROVIDER}:claude-sonnet-5", name="folded")
        by_item = Agent(MODELS["fast"], name="by_item")
        by_instance = Agent(config.model, name="by_instance")
        agent = Factory().build()
        """,
    )
    ids = {blueprint.label: blueprint.model.id if blueprint.model else None for blueprint in found.agents}
    assert ids == {
        "built": "anthropic:claude-opus-5",
        "folded": "anthropic:claude-sonnet-5",
        "by_item": "anthropic:claude-haiku-5",
        "by_instance": "anthropic:claude-opus-5",
    }
    assert agent(found, "built").settings.tool_retries == 3
    assert all(blueprint.warnings == [] for blueprint in found.agents)


def test_whatever_reads_a_value_at_run_time_gets_the_same_message_and_no_default(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        import json
        import os
        import sys
        from pathlib import Path
        from pydantic_ai import Agent
        from pydantic_settings import BaseSettings
        from vendor.config import load


        class Settings(BaseSettings):
            model: str = "openai:gpt-5.2"


        environment = Agent(os.getenv("MODEL", "openai:gpt-5.2"), name="environment")
        file = Agent(json.loads(Path("config.json").read_text())["model"], name="file")
        arguments = Agent(sys.argv[1], name="arguments")
        library = Agent(load("model", default="openai:gpt-5.2"), name="library")
        settings = Agent(Settings().model, name="settings")
        """,
    )
    for label, expression in (
        ("environment", 'os.getenv("MODEL", "openai:gpt-5.2")'),
        ("file", 'json.loads(Path("config.json").read_text())["model"]'),
        ("arguments", "sys.argv[1]"),
        ("library", 'load("model", default="openai:gpt-5.2")'),
        ("settings", "Settings().model"),  # pydantic-settings fills it from the environment
    ):
        found_agent = agent(found, label)
        assert found_agent.model is not None
        assert (found_agent.model.id, found_agent.model.provider, found_agent.model.name) == (None, None, expression)
        assert found_agent.model.expression == expression
        assert found_agent.warnings == [f"the model is set by `{expression}`, so it is only known when the code runs"]


def test_a_name_assigned_in_more_than_one_place_is_not_taken_for_its_last_value(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.models.openai import OpenAIChatModel


        def search(query: str) -> str:
            return query


        def fetch(url: str) -> str:
            return url


        model = OpenAIChatModel("gpt-5.2")
        tools = [search]
        if FAST:
            model = "openai:gpt-5-mini"
            tools = [search, fetch]

        agent = Agent(model, tools=tools)
        """,
    )
    found_agent = agent(found, "agent")
    assert found_agent.model is not None
    assert (found_agent.model.id, found_agent.model.name, found_agent.model.expression) == (None, "model", "model")
    # The tools of the list assigned last are shown, and said to be those of one case among others.
    assert [tool.name for tool in found_agent.tools] == ["search", "fetch"]
    assert found_agent.warnings == [
        "some of the tools in `tools` depend on a condition; those of every case are shown",
        "the model is set by `model`, so it is only known when the code runs",
    ]


def test_a_prompt_built_by_a_library_call_is_kept_as_its_source_written_out(tmp_path: Path) -> None:
    source = '''
        from textwrap import dedent
        from pydantic_ai import Agent

        agent = Agent(
            "test",
            instructions=dedent(
                """
                You are a careful assistant.
                Answer briefly.
                """
            ),
        )
        '''
    found = analyze_source(tmp_path, source)
    (instruction,) = agent(found, "agent").instructions
    assert (instruction.dynamic, instruction.text) == (True, None)
    # All of it, line breaks included, as the file has it: nothing is cut to fit a line.
    assert instruction.expression == (
        'dedent(\n        """\n        You are a careful assistant.\n        Answer briefly.\n        """\n    )'
    )
    assert agent(found, "agent").warnings == [
        'one of the instructions is the expression `dedent( """ You are a careful assistant. Answer briefly. """ )`; '
        "its text is only known when the code runs"
    ]


def test_the_packages_of_the_pydantic_ai_project_are_known_by_name(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai_harness import CodeMode
        from pydantic_evals.online_capability import OnlineEvaluation

        agent = Agent("test", capabilities=[CodeMode(), OnlineEvaluation(evaluators=[])])
        """,
    )
    assert [c.name for c in agent(found, "agent").capabilities] == ["CodeMode", "OnlineEvaluation"]
    assert agent(found, "agent").warnings == []


def test_another_librarys_classes_are_not_recognised_by_their_names(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent, ModelSettings
        from pydantic_ai.mcp import MCPServerStdio
        from vendor import MCPBridge, VendorSettings
        from vendor.models import BaseModel as VendorModel


        class Files(MCPServerStdio):
            pass


        class Answer(VendorModel):
            text: str


        mcp = Agent(
            "test",
            name="mcp",
            toolsets=[Files("npx", args=[]), MCPBridge("files")],
            model_settings=ModelSettings(temperature=0.5),
            output_type=Answer,
        )
        other = Agent("test", name="other", model_settings=VendorSettings(temperature=0.5))
        """,
    )
    mcp = agent(found, "mcp")
    # Derived from Pydantic AI's MCP server: an MCP server. Named like one, from elsewhere: unknown.
    assert [(t.kind, t.label, t.opaque) for t in mcp.toolsets] == [
        ("mcp", "Files", True),
        ("other", 'MCPBridge("files")', True),
    ]
    assert mcp.settings.model_settings == {"temperature": 0.5}
    # A base class called BaseModel that is not pydantic's says nothing about the fields.
    assert mcp.output.json_schema is None
    assert mcp.warnings == [
        'a toolset is built by `MCPBridge("files")`; its tools are only known when the code runs',
        "the schema of the output type `Answer` cannot be read from the analysed source, so it is not shown",
    ]
    other = agent(found, "other")
    assert other.settings.model_settings is None
    assert other.settings.expressions == {"model_settings": "VendorSettings(temperature=0.5)"}


FACTORY = '''
    import functools
    from pydantic_ai import Agent


    def make(name: str, prompt: str = "Be brief.", *, retries: int = 1) -> Agent:
        agent = Agent("test", name=name, instructions=prompt, retries=retries)

        @agent.tool_plain
        def today() -> str:
            """What day it is."""
            return "Monday"

        return agent


    @functools.cache
    def shared() -> Agent:
        return Agent("test", name="shared")


    researcher = make("researcher", "You research.", retries=3)
    writer = make("writer")
    options = {"prompt": "From a dict."}
    spread = make("spread", **options)
    once = shared()
    again = shared()


    @researcher.tool_plain
    def search(q: str) -> str:
        """Search the web."""
        return q


    @writer.tool_plain
    async def ask_researcher(q: str) -> str:
        """Ask the researcher."""
        return (await researcher.run(q)).output


    @once.tool_plain
    def ping() -> str:
        return "pong"
    '''


def test_a_function_called_in_several_places_makes_one_agent_per_call(tmp_path: Path) -> None:
    found = analyze_source(tmp_path, FACTORY)
    assert [blueprint.label for blueprint in found.agents] == ["researcher", "writer", "spread", "shared"]
    researcher, writer, spread, shared = found.agents
    # Each call's own arguments, and the parameters' defaults where a call leaves them out.
    assert [(i.text, i.expression) for i in researcher.instructions] == [("You research.", None)]
    assert [(i.text, i.expression) for i in writer.instructions] == [("Be brief.", None)]
    assert (researcher.settings.tool_retries, writer.settings.tool_retries) == (3, 1)
    assert [b.location.line for b in found.agents if b.location] == [7, 7, 7, 19]
    # What the function itself registers is on every agent it makes; what is added to one is on that one.
    assert [t.name for t in researcher.tools] == ["today", "search"]
    assert [t.name for t in writer.tools] == ["today", "ask_researcher"]
    assert [(d.tool_name, d.agent_id) for d in writer.delegates] == [("ask_researcher", researcher.id)]
    assert researcher.delegates == []
    assert (researcher.warnings, writer.warnings) == ([], [])
    # ``**options`` may hold any argument: the default is not taken for it.
    assert [(i.text, i.expression) for i in spread.instructions] == [(None, "prompt")]
    # A decorated function may hand out the same agent every time, as this cache does: one agent.
    assert [t.name for t in shared.tools] == ["ping"]


def test_calls_of_a_function_that_the_source_does_not_tell_apart_are_one_agent(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent


        class Guard:
            pass


        def make(model: str, reply: str = "", *, flaky: bool = False) -> Agent:
            agent = Agent(model, retries=3 if flaky else 1, capabilities=[Guard()] if flaky else [])

            @agent.tool_plain
            def lookup(key: str) -> str:
                return reply or key

            return agent


        def first_test():
            agent = make("test", "a")
            agent.run_sync("hi")


        def second_test():
            agent = make("test", "b")
            agent.run_sync("hello")


        def third_test():
            flaky = make("test", flaky=True)
            flaky.run_sync("hello")
        """,
    )
    # The first two differ in an argument the agent does not depend on: one agent, named as the function names it.
    assert [(b.id, b.label, b.settings.tool_retries, len(b.capabilities)) for b in found.agents] == [
        ("a1", "agent", 1, 0),
        ("a2", "flaky", 3, 1),
    ]
    assert [b.warnings for b in found.agents] == [[], []]


def test_a_function_called_once_makes_one_agent_named_after_its_call(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent


        def make(name: str) -> Agent:
            return Agent("test", name=name)


        only = make("only")
        """,
    )
    assert [(b.label, b.name, b.warnings) for b in found.agents] == [("only", "only", [])]


def test_a_name_that_is_the_agent_class_in_some_cases_only_still_makes_agents_and_says_so(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        try:
            from pydantic_ai import Agent
        except ImportError:
            Agent = None

        if USE_OTHER:
            from vendor import Agent as Other
        else:
            from pydantic_ai import Agent as Other

        bot = Agent("test", name="bot")
        other = Other("test", name="other")


        @bot.tool_plain
        def ping() -> str:
            return "pong"
        """,
    )
    assert [(b.label, [t.name for t in b.tools]) for b in found.agents] == [("bot", ["ping"]), ("other", [])]
    assert agent(found, "bot").warnings == [
        "`Agent` is Pydantic AI's `Agent` in some cases and not in others (it is given a value at lines 3 and 5); "
        "which one it is is decided when the code runs"
    ]
    assert agent(found, "other").warnings == [
        "`Other` is Pydantic AI's `Agent` in some cases and not in others (it is given a value at lines 8 and 10); "
        "which one it is is decided when the code runs"
    ]


def test_a_registration_method_held_in_a_name_registers_on_its_agent(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent

        boss = Agent("test", name="boss")
        register = boss.tool_plain
        instructions = boss.instructions


        @register
        def lookup(key: str) -> str:
            return key


        @register(name="find")
        def search(query: str) -> str:
            return query


        @instructions
        def today() -> str:
            return "Today is Monday."


        def fetch(url: str) -> str:
            return url


        register(fetch)
        """,
    )
    boss = agent(found, "boss")
    assert [t.name for t in boss.tools] == ["lookup", "find", "fetch"]
    assert [(i.kind, i.function) for i in boss.instructions] == [("instructions", "module.today")]
    assert boss.warnings == []


def test_an_agent_passed_to_a_call_is_delegated_to_only_where_the_call_runs_it(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        import logging
        from pydantic_ai import Agent

        log = logging.getLogger(__name__)
        boss = Agent("test", name="boss")
        worker = Agent("test", name="worker")
        seen: list[Agent] = []


        def run_it(agent: Agent, prompt: str) -> str:
            return agent.run_sync(prompt).output


        def pass_it_on(agent: Agent, prompt: str) -> str:
            return run_it(agent, prompt)


        def keep(agent: Agent) -> None:
            seen.append(agent)


        @boss.tool_plain
        def just_logs(text: str) -> str:
            log.info("worker is %s", worker)
            print(isinstance(worker, Agent), worker)
            keep(worker)
            return text


        @boss.tool_plain
        def through_a_helper(text: str) -> str:
            return run_it(worker, text)


        @boss.tool_plain
        def through_two(text: str) -> str:
            return pass_it_on(prompt=text, agent=worker)
        """,
    )
    assert [(d.tool_name, d.agent_id) for d in agent(found, "boss").delegates] == [
        ("through_a_helper", "a2"),
        ("through_two", "a2"),
    ]


def test_a_decorator_applied_before_the_tool_is_registered_is_named_in_a_warning(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        import functools
        from pydantic_ai import Agent
        from somewhere import cached

        boss = Agent("test", name="boss")
        other = Agent("test", name="other")


        def logged(function):
            @functools.wraps(function)
            def wrapper(*args, **kwargs):
                return function(*args, **kwargs)

            return wrapper


        def as_text(function):
            def wrapper(value):
                return str(function(value))

            return wrapper


        @boss.tool_plain
        @cached
        def lookup(key: str) -> str:
            return key


        @boss.tool_plain
        @as_text
        def double(value: int) -> int:
            return value * 2


        @boss.tool_plain
        @logged
        def kept(value: int) -> int:
            return value


        @boss.tool_plain
        @other.tool_plain
        def shared(value: int) -> int:
            return value
        """,
    )
    assert agent(found, "boss").warnings == [
        "the tool `lookup` is registered after `@cached` is applied to it; its parameters are read from the "
        "`def`, which `@cached` may change",
        "the tool `double` is registered after `@as_text` is applied to it; its parameters are read from the "
        "`def`, which `@as_text` may change",
        # Not read from the decorator's code either way: this one happens to keep the signature.
        "the tool `kept` is registered after `@logged` is applied to it; its parameters are read from the "
        "`def`, which `@logged` may change",
    ]
    assert agent(found, "other").warnings == []


def test_a_computed_value_reads_the_same_whether_it_is_written_inline_or_through_a_name(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent
        from prompts import load

        PROMPT = load("support")
        NAME = load("name")

        through_names = Agent("test", instructions=PROMPT, description=NAME)
        inline = Agent("test", instructions=load("support"), description=load("name"))
        """,
    )
    rows = [
        ([(i.dynamic, i.text, i.expression) for i in b.instructions], b.description, b.warnings) for b in found.agents
    ]
    assert rows[0] == rows[1]
    assert rows[0][0] == [(True, None, 'load("support")')]
    assert rows[0][2] == [
        'the description is computed by `load("name")`, so it is only known when the code runs',
        'one of the instructions is the expression `load("support")`; its text is only known when the code runs',
    ]


def test_str_format_is_read_like_any_other_value(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from datetime import date
        from pydantic_ai import Agent

        PROMPT = "You are {role}."

        pirate = Agent("test", instructions=PROMPT.format(role="a pirate"), name="{}-{n}".format("crew", n=1))
        numbered = Agent("test", instructions="Hi {0} and {x!r}".format("a", x="b"))
        today = Agent("test", instructions=PROMPT.format(role=date.today()))
        written = Agent("test", instructions=f"Today is {date.today()}.")
        """,
    )
    rows = {b.label: [(i.dynamic, i.text) for i in b.instructions] for b in found.agents}
    assert rows == {
        "crew-1": [(False, "You are a pirate.")],
        "numbered": [(False, "Hi a and 'b'")],
        "today": [(True, "You are {role}.")],
        "written": [(True, "Today is {date.today()}.")],
    }
    assert agent(found, "crew-1").warnings == agent(found, "numbered").warnings == []
    assert agent(found, "today").warnings == [
        "one of the instructions is the template `You are {role}.`; what is in braces is filled in when the code runs"
    ]
    assert agent(found, "written").warnings == [
        "one of the instructions is the template `Today is {date.today()}.`; what is in braces is filled in when "
        "the code runs"
    ]
