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
        "its text is only known at run time"
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
        "the output type `Answer` is defined outside the analysed source, so its schema is not shown",
    ]
    other = agent(found, "other")
    assert other.settings.model_settings is None
    assert other.settings.expressions == {"model_settings": "VendorSettings(temperature=0.5)"}
