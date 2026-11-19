"""Agents read from source: what is spelled out is a value, what is computed is its source text."""

from __future__ import annotations

from pathlib import Path

import pytest
from static_support import agent, analyze_source, fixture, one_agent, properties, tool

from pydantic_ai_viz import AgentBlueprint
from pydantic_ai_viz._models import ENVELOPE_ADAPTER, Instruction, SourceLocation, to_wire

# ------------------------------------------------------------ fully literal


def test_a_fully_spelled_out_agent_is_read_completely() -> None:
    found = fixture("basic_agent.py")
    assert (found.notices, found.files) == ([], ["basic_agent.py"])
    (shop,) = found.agents
    assert (shop.id, shop.name, shop.label, shop.description) == ("a1", "shop", "shop", "Resolves order problems")
    assert shop.source == "code"
    assert shop.location == SourceLocation(file="basic_agent.py", line=35)
    assert shop.deps_type == "basic_agent.ShopDeps"
    assert shop.warnings == []
    assert shop.native_tools == []
    assert shop.capabilities == []
    assert shop.delegates == []
    assert shop.settings.model_dump() == {
        "model_settings": {"temperature": 0.1, "max_tokens": 400},
        "end_strategy": "exhaustive",
        "tool_retries": 3,
        "output_retries": 2,
        "tool_timeout": 12.5,
        "expressions": {},
    }
    # Every blueprint the analyser makes is valid wire data.
    ENVELOPE_ADAPTER.validate_python({"seq": 1, "ts": 0.0, "type": "blueprint.agent", "agent": to_wire(shop)})


def test_instructions_keep_pydantic_ai_order_and_say_where_each_comes_from() -> None:
    shop = fixture("basic_agent.py").agents[0]
    assert [(i.kind, i.dynamic, i.text, i.function, i.location and i.location.line) for i in shop.instructions] == [
        (
            "instructions",
            False,
            "Check the order before you promise anything. Refund only what the policy allows.",
            None,
            42,
        ),
        ("instructions", True, None, "basic_agent.who_is_asking", 54),
        ("system_prompt", False, "You look after orders.", None, 45),  # through the constant BASE_PROMPT
        ("system_prompt", False, "Be brief.", None, 45),
        ("system_prompt", True, None, "basic_agent.house_rules", 59),
    ]
    assert all(instruction.expression is None for instruction in shop.instructions)


def test_tools_from_decorators_with_schemas_and_docstring_descriptions() -> None:
    shop = fixture("basic_agent.py").agents[0]
    assert [toolset.model_dump() for toolset in shop.toolsets] == [
        {
            "id": "ts0",
            "toolset_id": "<agent>",
            "label": "Agent tools",
            "kind": "agent",
            "wrappers": [],
            "opaque": False,
            "expression": None,
            "location": None,
        }
    ]
    assert [(t.name, t.takes_ctx, t.is_async, t.max_retries, t.kind) for t in shop.tools] == [
        ("get_order", True, True, None, "function"),
        ("shipping_cost", False, False, 4, "function"),
        ("lookup_policy", False, False, None, "function"),
        ("issue_refund", True, False, None, "unapproved"),
        ("ping", False, False, None, "function"),
    ]
    assert all(t.origin == "code" and t.toolset == "ts0" for t in shop.tools)

    get_order = tool(shop, "get_order")
    assert get_order.description == "Fetch an order."
    assert get_order.function == "basic_agent.get_order"
    assert get_order.location == SourceLocation(file="basic_agent.py", line=64)
    # The leading RunContext parameter is not an argument of the tool.
    assert get_order.parameters_schema == {
        "additionalProperties": False,
        "properties": {
            "order_id": {"description": "The order id, for example 'A-1042'.", "type": "string"},
            "include_items": {"default": False, "description": "Also list the items.", "type": "boolean"},
        },
        "required": ["order_id"],
        "type": "object",
    }
    assert get_order.return_schema == {"additionalProperties": {"type": "string"}, "type": "object"}

    # NumPy style.
    shipping = tool(shop, "shipping_cost")
    assert shipping.description == "Work out what shipping costs."
    assert properties(shipping.parameters_schema) == {
        "weight_kg": {"description": "Parcel weight in kilograms.", "type": "number"},
        "express": {"default": False, "description": "Next-day delivery.", "type": "boolean"},
        "zone": {"default": "eu", "description": "Where it goes.", "enum": ["eu", "world"], "type": "string"},
    }

    # Sphinx style; name and description come from the decorator, the field text from Annotated[..., Field()].
    policy = tool(shop, "lookup_policy")
    assert policy.description == "Find the policy text for a topic."
    assert policy.function == "basic_agent.policy"
    assert properties(policy.parameters_schema) == {
        "topic": {"description": "What the policy is about.", "type": "string"},
        "limit": {
            "default": 2,
            "description": "How many paragraphs.",
            "maximum": 5,
            "minimum": 1,
            "type": "integer",
        },
    }

    # A documented return value uses the form Pydantic AI sends to the model.
    assert tool(shop, "issue_refund").description == (
        "<summary>Refund an order.</summary>\n<returns>\n<description>A confirmation line.</description>\n</returns>"
    )
    ping = tool(shop, "ping")
    assert (ping.description, ping.parameters_schema) == (
        None,
        {"additionalProperties": False, "properties": {}, "type": "object"},
    )


def test_the_output_type_is_resolved_to_its_class() -> None:
    output = fixture("basic_agent.py").agents[0].output
    assert (output.mode, output.allows_text, output.type_name, output.tool_names) == ("auto", True, "Verdict", [])
    assert output.location == SourceLocation(file="basic_agent.py", line=26)
    assert output.json_schema == {
        "description": "What the agent decided.",
        "properties": {
            "status": {"enum": ["refunded", "replaced", "escalated"], "type": "string"},
            "refund_eur": {"description": "Amount refunded, 0 if none.", "minimum": 0, "type": "number"},
            "notes": {"items": {"type": "string"}, "type": "array"},
            "reviewer": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
        },
        "required": ["status", "refund_eur"],
        "title": "Verdict",
        "type": "object",
    }


# ------------------------------------------------------------ honest unknowns


def test_computed_values_are_reported_as_source_text_never_guessed() -> None:
    found = fixture("dynamic_values.py")
    assert found.notices == []
    computed = agent(found, "computed")

    # The model: an expression, nothing more is claimed about it.
    assert computed.model is not None
    assert computed.model.model_dump() == {
        "id": None,
        "name": "get_model()",
        "provider": None,
        "offline": False,
        "expression": "get_model()",
    }
    # A computed name leaves the variable name as the label.
    assert (computed.name, computed.label, computed.description) == (None, "computed", None)

    def shape(instruction: Instruction) -> tuple[bool, str | None, str | None, str | None]:
        return (instruction.dynamic, instruction.text, instruction.function, instruction.expression)

    assert [shape(i) for i in computed.instructions] == [
        # What PREAMBLE was given, as it would read inline: a call of a library function is not evaluated.
        (
            True,
            None,
            None,
            'dedent(\n    """\n    You are part of the support team.\n    Stay polite.\n    """\n).strip()',
        ),
        (True, "You work for the {TEAM} team, tier {os.environ.get('TIER', 'basic')!r}.", None, None),
        (True, None, None, 'PROMPTS["support"]'),
        (True, None, None, "house_style"),
        (True, None, None, "lambda ctx: ctx.deps.motto"),
        (True, None, None, "GREETING"),  # assigned twice in the file
        (True, None, "dynamic_values.with_context", None),
        (True, None, None, 'PREAMBLE + " Answer in English."'),
    ]

    assert computed.settings.model_dump() == {
        "model_settings": {"temperature": 0.2},
        "end_strategy": None,
        "tool_retries": 4,  # through the constant RETRIES
        "output_retries": 4,
        "tool_timeout": None,
        "expressions": {
            "end_strategy": 'os.getenv("END_STRATEGY", "early")',
            "tool_timeout": 'settings_for("timeout")',
            "model_settings.max_tokens": 'settings_for("max_tokens")',
        },
    }
    assert computed.warnings == [
        'the name is computed by `f"support-{TEAM}"`, so it is only known when the code runs',
        'the description is computed by `house_style("support")`, so it is only known when the code runs',
        "the model is set by `get_model()`, so it is only known when the code runs",
        'one of the instructions is the expression `dedent( """ You are part of the support team. Stay polite. """ )'
        ".strip()`; its text is only known when the code runs",
        'one of the instructions is the expression `PROMPTS["support"]`; its text is only known when the code runs',
        "one of the instructions is `house_style` (my_company.prompts.house_style), which is defined outside the "
        "analysed source; whether it is text or a function is not known",
        "one of the instructions is the expression `GREETING`; its text is only known when the code runs",
        'one of the system prompts is the expression `PREAMBLE + " Answer in English."`; '
        "its text is only known when the code runs",
    ]


def test_models_given_in_other_ways() -> None:
    found = fixture("dynamic_values.py")
    by_constant = agent(found, "by_constant")
    assert by_constant.model is not None
    assert by_constant.model.model_dump() == {
        "id": "openai:gpt-5.2",
        "name": "gpt-5.2",
        "provider": "openai",
        "offline": False,
        "expression": None,
    }
    assert by_constant.settings.expressions == {
        "tool_retries": "compute_retries()",
        "output_retries": "compute_retries()",
        "model_settings": 'settings_for("all")',
    }
    assert by_constant.settings.model_settings is None
    assert by_constant.warnings == []  # the expressions are data, not a degradation of something else

    by_class = agent(found, "by_class")
    assert by_class.model is not None
    # A Pydantic AI model class given a literal name says which model it is; the rest stays an expression.
    assert by_class.model.model_dump() == {
        "id": None,
        "name": "gpt-5.2",
        "provider": None,
        "offline": False,
        "expression": 'OpenAIChatModel("gpt-5.2", provider=make_provider())',
    }
    assert by_class.warnings == []

    by_env = agent(found, "by_env")
    assert by_env.model is not None
    assert by_env.model.expression == 'os.getenv("MODEL", "openai:gpt-5.2")'  # the default is not taken as the value

    assert agent(found, "no_model").model is None
    splatted = agent(found, "splatted")
    # ``**OPTIONS`` is a dict the file writes out, so what is in it is read like any other argument.
    assert (splatted.name, splatted.settings.tool_retries) == ("splatted", 2)
    assert splatted.warnings == []


def test_offline_model_classes_are_recognised_but_not_named(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.models.test import TestModel
        from somewhere import TestModel as Impostor

        tested = Agent(TestModel(custom_output_text="hi"))
        scripted = Agent(FunctionModel(respond), name="scripted")
        shared_model = TestModel()
        shared = Agent(shared_model)
        impostor = Agent(Impostor())
        strings = [Agent("test"), Agent("function:scripted"), Agent("anthropic:claude-x"), Agent(model=None)]
        """,
    )
    models = {blueprint.label: blueprint.model for blueprint in found.agents}
    tested = models["tested"]
    assert tested is not None
    assert tested.model_dump() == {
        "id": None,
        "name": 'TestModel(custom_output_text="hi")',
        "provider": "test",
        "offline": True,
        "expression": 'TestModel(custom_output_text="hi")',
    }
    assert agent(found, "tested").warnings == []
    scripted, shared, impostor = models["scripted"], models["shared"], models["impostor"]
    assert scripted is not None
    assert shared is not None
    assert impostor is not None
    assert (scripted.provider, scripted.offline) == ("function", True)
    assert (shared.provider, shared.offline, shared.expression) == (
        "test",
        True,
        "TestModel()",
    )  # what the variable was assigned
    assert (impostor.provider, impostor.offline) == (None, False)
    listed = [models[f"agent-{index}"] for index in range(5, 9)]
    assert [(m.id, m.name, m.provider, m.offline) if m else None for m in listed] == [
        ("test", "test", None, False),
        ("function:scripted", "scripted", "function", True),
        ("anthropic:claude-x", "claude-x", "anthropic", False),
        None,
    ]


def test_a_model_class_given_a_literal_name_names_the_model(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        import os
        from pydantic_ai import Agent
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.models.fallback import FallbackModel
        from pydantic_ai.models.openai import OpenAIChatModel
        from my_models import LocalModel

        shared_model = OpenAIChatModel("gpt-5.2", provider=make_provider())

        def build_model():
            return AnthropicModel(model_name="claude-sonnet-5")

        through_variable = Agent(shared_model)
        through_factory = Agent(build_model())
        from_environment = Agent(OpenAIChatModel(os.environ["MODEL"]))
        wrapped = Agent(FallbackModel(shared_model, build_model()))
        not_ours = Agent(LocalModel("llama"))
        """,
    )
    models = {blueprint.label: blueprint.model for blueprint in found.agents}
    warnings = {blueprint.label: blueprint.warnings for blueprint in found.agents}
    assert [(label, m.name, m.expression, m.id, m.provider) for label, m in models.items() if m] == [
        ("through_variable", "gpt-5.2", 'OpenAIChatModel("gpt-5.2", provider=make_provider())', None, None),
        ("through_factory", "claude-sonnet-5", "build_model()", None, None),
        # A computed name, a model that wraps others, a class from elsewhere: only the expression is known.
        (
            "from_environment",
            'OpenAIChatModel(os.environ["MODEL"])',
            'OpenAIChatModel(os.environ["MODEL"])',
            None,
            None,
        ),
        (
            "wrapped",
            "FallbackModel(shared_model, build_model())",
            "FallbackModel(shared_model, build_model())",
            None,
            None,
        ),
        ("not_ours", 'LocalModel("llama")', 'LocalModel("llama")', None, None),
    ]
    assert (warnings["through_variable"], warnings["through_factory"]) == ([], [])
    assert warnings["from_environment"] == [
        'the model is set by `OpenAIChatModel(os.environ["MODEL"])`, so it is only known when the code runs'
    ]
    assert len(warnings["wrapped"]) == len(warnings["not_ours"]) == 1


def test_arguments_from_earlier_pydantic_ai_releases_are_read_too(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic import BaseModel
        from pydantic_ai import Agent, CodeExecutionTool, WebSearchTool
        from pydantic_ai.builtin_tools import SomeLaterTool
        from pydantic_ai.mcp import MCPServerStdio

        class Box(BaseModel):
            width: int

        server = MCPServerStdio("uv", args=["run", "server.py"])

        old = Agent(
            "openai:gpt-4o",
            result_type=Box,
            retries=2,
            result_retries=5,
            mcp_servers=[server],
            builtin_tools=[WebSearchTool(), CodeExecutionTool(), SomeLaterTool(), WebSearchTool(), make_tool()],
        )
        middle = Agent("openai:gpt-4o", output_type=Box, retries=compute(), output_retries=1, builtin_tools=TOOLS)
        both = Agent("openai:gpt-4o", output_type=str, result_type=Box, output_retries=compute())
        """,
    )
    old, middle, both = found.agents
    assert (old.output.type_name, old.output.mode) == ("Box", "auto")
    assert (old.settings.tool_retries, old.settings.output_retries) == (2, 5)
    assert [(t.kind, t.label) for t in old.toolsets] == [("mcp", "MCPServerStdio")]
    # Known classes by the kind of tool they are, an unknown one by its class name, each once.
    assert old.native_tools == ["web_search", "code_execution", "SomeLaterTool"]
    assert old.warnings == ["a builtin tool is built by `make_tool()`; its kind is only known when the code runs"]

    assert (middle.settings.tool_retries, middle.settings.output_retries) == (None, 1)
    assert middle.settings.expressions == {"tool_retries": "compute()"}
    assert middle.native_tools == []
    assert middle.warnings == ["the builtin tools are built by `TOOLS`; they are only known when the code runs"]

    assert both.output.type_name == "str"  # the name in use today wins
    assert both.settings.expressions == {"output_retries": "compute()"}


def test_a_long_expression_is_cut_on_the_wire_and_shorter_still_in_the_warning(tmp_path: Path) -> None:
    arguments = ", ".join(f"option_{index}=value_{index}" for index in range(40))
    blueprint = one_agent(tmp_path, f"from pydantic_ai import Agent\nagent = Agent(build_model({arguments}))\n")
    assert blueprint.model is not None
    expression = blueprint.model.expression
    assert expression is not None
    assert len(expression) == 200
    assert expression.endswith("...")
    (warning,) = blueprint.warnings
    assert len(warning) < 160
    assert warning.startswith("the model is set by `build_model(option_0=value_0, ")


def test_deps_type_forms(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from dataclasses import dataclass
        from pydantic_ai import Agent
        from services import Database

        @dataclass
        class Deps:
            db: Database

        local = Agent("test", deps_type=Deps)
        imported = Agent("test", deps_type=Database)
        builtin = Agent("test", deps_type=str)
        generic = Agent("test", deps_type=dict[str, Database])
        nothing = Agent("test", deps_type=None)
        anything = Agent("test", deps_type=object)
        unset = Agent("test")
        """,
    )
    assert {blueprint.label: blueprint.deps_type for blueprint in found.agents} == {
        "local": "module.Deps",
        "imported": "services.Database",
        "builtin": "str",
        "generic": "dict[str, Database]",
        "nothing": None,
        "anything": None,
        "unset": None,
    }


def test_capabilities_are_listed_by_class_name(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.capabilities import Thinking, ToolSearch
        import pydantic_ai.capabilities as caps
        from vendor.capabilities import VendorGuard

        class Audit:
            pass

        thinking = Thinking(effort="high")
        listed = Agent(
            "test",
            capabilities=[thinking, caps.Hooks(), ToolSearch(), Audit(), make_capability(), VendorGuard(), *MORE],
        )
        MORE = [Thinking()]
        computed = Agent("test", capabilities=load_capabilities())
        none = Agent("test", capabilities=None)
        """,
    )
    listed = agent(found, "listed")
    assert [(c.name, c.builtin) for c in listed.capabilities] == [
        ("Thinking", False),
        ("Hooks", False),
        ("ToolSearch", True),
        ("Audit", False),
        ("make_capability()", False),
        ("VendorGuard()", False),  # another library's: a class by its name, but nothing in the source says so
        ("Thinking", False),
    ]
    assert listed.warnings == [
        "a capability is built by `make_capability()`; its type is only known when the code runs",
        "a capability is built by `VendorGuard()`; its type is only known when the code runs",
    ]
    computed = agent(found, "computed")
    assert computed.capabilities == []
    assert computed.warnings == [
        "the capabilities are built by `load_capabilities()`; they are only known when the code runs"
    ]
    assert agent(found, "none").warnings == []


def test_retries_forms(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent
        both = Agent("test", retries=2)
        split = Agent("test", retries={"tools": 5})
        truthy = Agent("test", retries=True)
        unset = Agent("test", retries=None)
        """,
    )
    retries = {b.label: (b.settings.tool_retries, b.settings.output_retries) for b in found.agents}
    assert retries == {"both": (2, 2), "split": (5, None), "truthy": (None, None), "unset": (None, None)}
    assert agent(found, "truthy").settings.expressions == {"tool_retries": "True", "output_retries": "True"}


# ------------------------------------------------------- tools and toolsets


def test_tools_given_as_arguments_and_tools_that_cannot_be_read() -> None:
    worker = agent(fixture("toolsets.py"), "worker")
    own = [t for t in worker.tools if t.toolset == "ts0"]
    assert [(t.name, t.takes_ctx, t.function) for t in own] == [
        ("by_hand", False, "toolsets.by_hand"),
        ("needs_ctx", True, "toolsets.needs_ctx"),  # read from the RunContext annotation
        ("ratio", False, "toolsets.divide"),  # Tool(divide, name="ratio")
        ("fetch_page", None, "external_tools.fetch_page"),
        ("add", False, "toolsets.add"),  # from *COMMON
        ("divide", False, "toolsets.divide"),
        ("own_tool", False, "toolsets.own_tool"),  # decorators come after the tools argument
    ]
    assert list(properties(tool(worker, "needs_ctx").parameters_schema)) == ["n"]
    external = tool(worker, "fetch_page")
    assert (external.parameters_schema, external.description, external.is_async) == (None, None, None)
    assert external.location == SourceLocation(file="toolsets.py", line=71)
    assert worker.warnings == [
        "a tool is built by `make_tool()`; it cannot be described without running the code",
        # Another library's class, whatever it is called: what it holds is known when the code runs.
        'a toolset is built by `VendorToolset(region="eu")`; its tools are only known when the code runs',
        'a toolset is built by `build_toolset("extra")`; its tools are only known when the code runs',
        "the tool `fetch_page` is `external_tools.fetch_page`, which is defined outside the analysed source, "
        "so its description and parameters are not shown",
    ]


def test_toolsets_of_every_kind() -> None:
    worker = agent(fixture("toolsets.py"), "worker")
    rows = [(t.id, t.kind, t.label, t.toolset_id, t.wrappers, t.opaque, t.expression) for t in worker.toolsets]
    assert rows == [
        ("ts0", "agent", "Agent tools", "<agent>", [], False, None),
        ("ts1", "function", "FunctionToolset 'kb'", "kb", [], False, None),
        ("ts2", "function", "FunctionToolset", None, ["PrefixedToolset"], False, None),
        (
            "ts3",
            "function",
            "FunctionToolset 'constructed'",
            "constructed",
            ["PrefixedToolset", "FilteredToolset"],
            False,
            None,
        ),
        ("ts4", "function", "FunctionToolset 'kb'", "kb", ["PrefixedToolset"], False, None),
        ("ts5", "mcp", "MCPServerStdio 'files'", "files", [], True, None),
        ("ts6", "mcp", "MCPServerStdio", None, ["PrefixedToolset"], True, None),
        ("ts7", "other", 'VendorToolset(region="eu")', None, [], True, 'VendorToolset(region="eu")'),
        ("ts8", "other", 'build_toolset("extra")', None, [], True, 'build_toolset("extra")'),
        ("ts9", "dynamic", "per_run", None, [], True, None),
        ("ts10", "function", "FunctionToolset", None, [], False, None),  # CombinedToolset is flattened
        ("ts11", "mcp", "MCPServerStdio 'files'", "files", [], True, None),
        ("ts12", "dynamic", "built_per_run", None, [], True, None),  # @worker.toolset
    ]
    # Where each toolset is constructed, not where it is listed.
    lines = {t.id: t.location.line if t.location else None for t in worker.toolsets}
    assert (lines["ts0"], lines["ts1"], lines["ts5"], lines["ts9"], lines["ts12"]) == (None, 9, 50, 64, 94)

    by_toolset: dict[str, list[tuple[str, bool | None, int | None]]] = {}
    for item in worker.tools:
        by_toolset.setdefault(item.toolset, []).append((item.name, item.takes_ctx, item.max_retries))
    assert by_toolset["ts1"] == [("search_kb", False, None), ("read_article", True, 2)]
    # add_function reads takes_ctx from the annotation; add_tool(Tool(...)) keeps the Tool's name.
    assert by_toolset["ts2"] == [("math_add", False, None), ("math_multiply", True, 5), ("math_div", False, None)]
    assert by_toolset["ts3"] == [("c_add", False, None), ("c_multiply", True, 1)]  # FunctionToolset([...])
    assert by_toolset["ts4"] == [("again_search_kb", False, None), ("again_read_article", True, 2)]
    assert by_toolset["ts10"] == [("add", False, None), ("multiply", True, 5), ("div", False, None)]
    assert all(key in by_toolset for key in ("ts0", "ts1", "ts2", "ts3", "ts4", "ts10"))
    assert tool(worker, "math_div").description == "Divide a by b."
    assert tool(worker, "search_kb").description == "Search the knowledge base."


def test_tool_and_toolset_lists_that_are_computed_are_marked_opaque() -> None:
    unknown = agent(fixture("toolsets.py"), "unknown_lists")
    assert unknown.tools == []
    assert [(t.kind, t.label, t.opaque, t.expression) for t in unknown.toolsets] == [
        ("other", "load_toolsets()", True, "load_toolsets()")
    ]
    assert unknown.warnings == [
        "the tools are built by `load_tools()`; they are only known when the code runs",
        "the toolsets are built by `load_toolsets()`; they are only known when the code runs",
    ]


def test_a_computed_prefix_is_not_invented(tmp_path: Path) -> None:
    blueprint = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent
        from pydantic_ai.toolsets import FunctionToolset

        tools = FunctionToolset()

        @tools.tool_plain
        def lookup(key: str) -> str:
            return key

        agent = Agent("test", toolsets=[tools.prefixed(PREFIX)])
        """,
    )
    assert [t.name for t in blueprint.tools] == ["lookup"]
    assert blueprint.toolsets[0].wrappers == ["PrefixedToolset"]
    assert blueprint.warnings == [
        "a tool name prefix in `tools.prefixed(PREFIX)` is computed; the tool names shown may lack it"
    ]


def test_tool_registration_forms_that_are_not_registrations_are_ignored(tmp_path: Path) -> None:
    blueprint = one_agent(
        tmp_path,
        """
        from pydantic_ai import Agent

        agent = Agent("test")
        other = object()

        @other.tool
        def not_ours(x: int) -> int:
            return x

        @agent.tool_plain
        @staticmethod
        def stacked(x: int) -> int:
            '''Decorators may be stacked.'''
            return x

        @agent.output_validator
        def check(value: str) -> str:
            return value

        agent.tool_plain()
        agent.tool_plain("not a function")
        agent.tool_plain(*functions)
        agent.run_sync("hello")
        agent.unknown_method(stacked)
        """,
    )
    assert [t.name for t in blueprint.tools] == ["stacked"]
    assert blueprint.instructions == []


# ------------------------------------------------------------- delegation


def test_delegation_edges_come_from_tool_bodies() -> None:
    found = fixture("delegation.py")
    ids = {blueprint.label: blueprint.id for blueprint in found.agents}
    assert ids == {"lead": "a1", "writer": "a2", "reviewer": "a3", "silent": "a4"}
    lead = agent(found, "lead")
    assert [(d.tool_name, d.agent_id, d.evidence) for d in lead.delegates] == [
        ("ask_writer", "a2", "code"),  # the agent is defined below the tool
        ("ask_reviewer", "a3", "code"),  # through the alias `checker`
        ("ask_through_helper", "a2", "code"),  # handed to a helper function
        ("ask_both", "a2", "code"),
        ("ask_both", "a3", "code"),  # run_stream
        ("try_again", "a1", "code"),  # its own agent
    ]
    # Reading an attribute of an agent is not running it.
    assert not any(d.tool_name == "just_mentions" for d in lead.delegates)
    assert [(d.tool_name, d.agent_id) for d in agent(found, "writer").delegates] == [("iterate", "a3")]
    assert agent(found, "reviewer").delegates == []


# ----------------------------------------------- where agents are constructed


def test_agents_in_factories_classes_and_containers_are_found() -> None:
    found = fixture("factories.py")
    assert [(b.label, b.name) for b in found.agents] == [
        ("from_factory", None),  # the variable the factory's one call is assigned to
        ("direct", "direct"),  # returned straight from a function
        ("made_async", "made_async"),
        ("shared", "shared"),  # a class attribute, built with pydantic_ai.Agent through a module alias
        ("planner", "planner"),  # self.planner = Agent[None, str](...)
        ("helper", None),
        ("first", "first"),  # inside a dict
        ("agent-8", None),  # nothing names it
        ("in_main", "in_main"),  # under `if __name__ == "__main__":`
        ("guarded", "guarded"),
        ("one", "one"),
        ("two", "two"),
    ]


def test_tools_follow_the_agent_through_a_factory_and_through_self() -> None:
    found = fixture("factories.py")
    made = agent(found, "from_factory")
    assert [(t.name, t.function) for t in made.tools] == [
        ("inside", "factories.build_agent.<locals>.inside"),
        ("outside", "factories.outside"),  # registered on `from_factory = build_agent()`
    ]
    planner = agent(found, "planner")
    assert [(t.name, t.takes_ctx, t.function) for t in planner.tools] == [
        ("estimate", False, "factories.Team.estimate"),  # self.planner.tool_plain(self.estimate)
        ("delegate", True, "factories.Team.__init__.<locals>.delegate"),
    ]
    # `self` is not an argument of a tool that is a method.
    assert list(properties(tool(planner, "estimate").parameters_schema)) == ["task"]
    assert [(d.tool_name, d.agent_id) for d in planner.delegates] == [("delegate", agent(found, "helper").id)]


def test_a_reassigned_name_gives_each_agent_the_tools_registered_while_it_held_the_name() -> None:
    found = fixture("factories.py")
    assert [t.name for t in agent(found, "one").tools] == ["for_one"]
    assert [t.name for t in agent(found, "two").tools] == ["for_two"]


def test_only_pydantic_ai_agents_are_agents() -> None:
    found = fixture("factories.py")
    names = {blueprint.name for blueprint in found.agents}
    assert "lookalike" not in names  # a class of the file that is called Agent
    assert "from_parameter" not in names  # a parameter called Agent


@pytest.mark.parametrize(
    "source",
    [
        "Agent('test', name='unbound')\n",  # the name Agent is never imported
        "from agents_lib import Agent\na = Agent('test')\n",
        "import pydantic_ai\nAgent = make_class()\na = Agent('test')\n",
        "from pydantic_ai import Agent\na = Agent\n",  # never called
        "from pydantic_ai import Agent\na = Agent.from_spec(spec)\n",
        "from pydantic_ai import Agent\nclass Mine(Agent): pass\na = Mine('test')\n",
        "# from pydantic_ai import Agent\n# a = Agent('test')\n",
        "text = '''\nfrom pydantic_ai import Agent\na = Agent(\"test\")\n'''\n",
    ],
)
def test_things_that_are_not_agent_constructions(tmp_path: Path, source: str) -> None:
    assert analyze_source(tmp_path, source).agents == []


@pytest.mark.parametrize(
    "source",
    [
        "from pydantic_ai import Agent\na = Agent('test')\n",
        "import pydantic_ai\na = pydantic_ai.Agent('test')\n",
        "import pydantic_ai as p\na = p.Agent[None, str]('test')\n",
        "from pydantic_ai import Agent as A\na: A[None, str] = A('test')\n",
        "from pydantic_ai.agent import Agent\na = Agent(model='test')\n",
        "from pydantic_ai import *\na = Agent('test')\n",
        "from pydantic_ai import Agent\nNew = Agent\na = New('test')\n",
        "from pydantic_ai import Agent\na = b = Agent('test')\n",
        "from pydantic_ai import Agent\nasync def f():\n    a = await wrap(Agent('test'))\n",
        "from pydantic_ai import Agent\nprint(Agent('test').run_sync('hi').output)\n",
        "from pydantic_ai import Agent\nagents = [Agent('test') for _ in range(3)]\n",
        "from pydantic_ai import Agent\nif flag:\n    a = Agent('test')\n",
        "from pydantic_ai import Agent\ndef f(a=Agent('test')): ...\n",
        "from pydantic_ai import Agent\nwith ctx() as c:\n    a = Agent('test')\n",
        "from pydantic_ai import Agent\nclass C:\n    a = Agent('test')\n",
        "from pydantic_ai import Agent\nmatch x:\n    case 1:\n        a = Agent('test')\n",
    ],
)
def test_every_way_of_calling_the_agent_class_is_found(tmp_path: Path, source: str) -> None:
    found = analyze_source(tmp_path, source)
    assert len(found.agents) == 1
    assert found.agents[0].source == "code"


def test_labels_fall_back_from_name_to_variable_to_number(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from pydantic_ai import Agent

        named = Agent("test", name="given")
        variable = Agent("test")
        typed: Agent = Agent("test")
        first, second = Agent("test"), Agent("test")
        run(Agent("test"))
        empty = Agent("test", name="")

        def factory():
            return Agent("test")
        """,
    )
    assert [(b.id, b.label, b.name) for b in found.agents] == [
        ("a1", "given", "given"),
        ("a2", "variable", None),
        ("a3", "typed", None),
        ("a4", "first", None),
        ("a5", "second", None),
        ("a6", "agent-6", None),
        ("a7", "empty", None),
        ("a8", "factory", None),
    ]


def test_every_blueprint_of_every_fixture_is_valid_wire_data() -> None:
    names = ["basic_agent.py", "dynamic_values.py", "toolsets.py", "delegation.py", "factories.py", "outputs.py"]
    graphs = ["graphs.py", "graphs_nodes.py", "graphs_reachable.py", "graphs_classic.py", "graphs_unreadable.py"]
    for name in [*names, *graphs, "tool_signatures.py", "hostile_side_effects.py"]:
        found = fixture(name)
        assert found.blueprints, name
        for blueprint in found.blueprints:
            assert type(blueprint).model_validate(blueprint.model_dump(mode="json")) == blueprint
            if isinstance(blueprint, AgentBlueprint):
                toolsets = {toolset.id for toolset in blueprint.toolsets}
                assert all(item.toolset in toolsets for item in blueprint.tools), name
                known = {other.id for other in found.agents}
                assert all(delegate.agent_id in known for delegate in blueprint.delegates), name
