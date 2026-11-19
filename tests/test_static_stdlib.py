"""The standard-library features the analyser evaluates or represents, and only those.

The allow-list (``ALLOWED_STANDARD_LIBRARY`` in test_static_guards.py):

* ``os.getenv``, ``os.environ.get`` and ``os.environ[...]``: an environment variable, reported as
  the variable and the default the source gives, never as a value;
* ``textwrap.dedent`` and ``inspect.cleandoc``: pure functions of a literal string, evaluated by
  calling the real function on it.

Anything else, and anything these are given that is not a literal, is shown as its source text.
"""

from __future__ import annotations

import inspect
import textwrap
from pathlib import Path

import pytest
from static_support import agent, analyze_source, fixture

from pydantic_ai_viz import AgentBlueprint, Analysis, analyze
from pydantic_ai_viz._models import EnvironmentValue

# -------------------------------------------------------- environment variables

ENVIRONMENT_FORMS = """
import os
import os as system
from os import environ, getenv
from os import environ as env_vars, getenv as read_env
from pydantic_ai import Agent

NAME = "MODEL"
DEFAULT = "openai:gpt-5.2"
ENVIRONMENT = os.environ

getenv_default = Agent(os.getenv("MODEL", "openai:gpt-5.2"))
getenv_none = Agent(os.getenv("MODEL"))
getenv_default_none = Agent(os.getenv("MODEL", None))
getenv_keywords = Agent(os.getenv(key="MODEL", default="openai:gpt-5.2"))
getenv_keyword_default = Agent(os.getenv("MODEL", default="openai:gpt-5.2"))
get_default = Agent(os.environ.get("MODEL", "openai:gpt-5.2"))
get_none = Agent(os.environ.get("MODEL"))
subscript = Agent(os.environ["MODEL"])
imported_getenv = Agent(getenv("MODEL", "openai:gpt-5.2"))
imported_environ = Agent(environ.get("MODEL", "openai:gpt-5.2"))
imported_subscript = Agent(environ["MODEL"])
aliased_module = Agent(system.getenv("MODEL", "openai:gpt-5.2"))
aliased_getenv = Agent(read_env("MODEL", "openai:gpt-5.2"))
aliased_environ = Agent(env_vars["MODEL"])
held_mapping = Agent(ENVIRONMENT.get("MODEL", "openai:gpt-5.2"))
named_variable = Agent(os.getenv(NAME, DEFAULT))
"""


def _model(found: Analysis, label: str) -> AgentBlueprint:
    blueprint = agent(found, label)
    assert blueprint.model is not None
    return blueprint


@pytest.mark.parametrize(
    ("label", "default"),
    [
        ("getenv_default", "openai:gpt-5.2"),
        ("getenv_none", None),
        ("getenv_default_none", None),
        ("getenv_keywords", "openai:gpt-5.2"),
        ("getenv_keyword_default", "openai:gpt-5.2"),
        ("get_default", "openai:gpt-5.2"),
        ("get_none", None),
        ("subscript", None),
        ("imported_getenv", "openai:gpt-5.2"),
        ("imported_environ", "openai:gpt-5.2"),
        ("imported_subscript", None),
        ("aliased_module", "openai:gpt-5.2"),
        ("aliased_getenv", "openai:gpt-5.2"),
        ("aliased_environ", None),
        ("held_mapping", "openai:gpt-5.2"),
        ("named_variable", "openai:gpt-5.2"),  # the name and the default through constants
    ],
)
def test_a_model_read_from_the_environment_is_the_variable_and_the_default_never_the_value(
    tmp_path: Path, label: str, default: str | None
) -> None:
    found = analyze_source(tmp_path, ENVIRONMENT_FORMS)
    blueprint = _model(found, label)
    model = blueprint.model
    assert model is not None
    assert model.env == EnvironmentValue(variable="MODEL", default=default)
    # Not a model id, not a provider: the default is not the value, and the expression stays the expression.
    assert (model.id, model.provider, model.offline, model.model_class) == (None, None, False, None)
    assert model.expression is not None
    assert model.name == model.expression
    said = "the source gives no default" if default is None else f"the source's default is `{default}`"
    assert blueprint.warnings == [f"the model comes from the environment variable `MODEL` when the code runs; {said}"]


def test_what_is_not_a_literal_read_of_the_environment_stays_the_expression(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        import os
        from my_tools import getenv
        from pydantic_ai import Agent

        CHOICE = "A"
        CHOICE = "B"  # assigned twice: which name is read is decided at run time

        computed_name = Agent(os.getenv(prefix + "MODEL", "openai:gpt-5.2"))
        computed_default = Agent(os.getenv("MODEL", pick_default()))
        number_default = Agent(os.getenv("MODEL", 3))
        two_names = Agent(os.getenv(CHOICE))
        spread = Agent(os.getenv(*arguments))
        options = Agent(os.environ.get("MODEL", **options))
        too_many = Agent(os.getenv("MODEL", "a", "b"))
        unknown_keyword = Agent(os.getenv(name="MODEL"))
        or_default = Agent(os.getenv("MODEL") or "openai:gpt-5.2")
        not_os = Agent(getenv("MODEL", "openai:gpt-5.2"))
        other_mapping = Agent(config["MODEL"])
        computed_key = Agent(os.environ[key])
        sliced = Agent(os.environ["MODEL"][:3])
        """,
    )
    for blueprint in found.agents:
        model = blueprint.model
        assert model is not None
        assert model.env is None, blueprint.label
        assert blueprint.warnings == [
            f"the model is set by `{model.expression}`, so it is only known when the code runs"
        ], blueprint.label


def test_a_model_class_given_a_name_from_the_environment_says_both(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        import os
        from pydantic_ai import Agent
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.models.openai import OpenAIChatModel

        positional = Agent(OpenAIChatModel(os.getenv("LLM", "gpt-5.2"), provider=make_provider()))
        keyword = Agent(AnthropicModel(model_name=os.environ["LLM"]))
        """,
    )
    positional = _model(found, "positional")
    assert positional.model is not None
    assert positional.model.model_class == "OpenAIChatModel"
    assert positional.model.env == EnvironmentValue(variable="LLM", default="gpt-5.2")
    assert positional.model.name == positional.model.expression
    assert positional.warnings == [
        "the model name of `OpenAIChatModel` comes from the environment variable `LLM` when the code runs; "
        "the source's default is `gpt-5.2`"
    ]
    keyword = _model(found, "keyword")
    assert keyword.model is not None
    assert (keyword.model.model_class, keyword.model.env) == (
        "AnthropicModel",
        EnvironmentValue(variable="LLM", default=None),
    )


def test_the_environment_is_followed_through_the_functions_of_the_project() -> None:
    # As in a course's ``llm.py``: ``get_model()`` returns ``OpenAIChatModel(model_id(), provider=...)`` and
    # ``model_id()`` returns ``os.getenv("ACME_MODEL", DEFAULT_MODEL)``, in another file.
    found = fixture("environment/exercise.py")
    assert found.files == ["exercise.py", "llm.py"]
    tools_agent = agent(found, "tools_agent")
    assert tools_agent.model is not None
    assert tools_agent.model.model_dump() == {
        "id": None,
        "name": "get_model()",
        "provider": None,
        "offline": False,
        "expression": "get_model()",
        "model_class": "OpenAIChatModel",
        "env": {"variable": "ACME_MODEL", "default": "acme-pass/fast-model-1017"},
    }
    assert tools_agent.warnings == [
        "the model name of `OpenAIChatModel` comes from the environment variable `ACME_MODEL` when the code "
        "runs; the source's default is `acme-pass/fast-model-1017`"
    ]
    by_id = agent(found, "by_id")
    assert by_id.model is not None
    assert (by_id.model.expression, by_id.model.model_class) == ("model_id()", None)
    assert by_id.model.env == EnvironmentValue(variable="ACME_MODEL", default="acme-pass/fast-model-1017")
    required = agent(found, "required")
    assert required.model is not None
    assert required.model.env == EnvironmentValue(variable="ACME_REQUIRED_MODEL", default=None)
    assert required.model.model_class == "OpenAIChatModel"


def test_only_the_model_is_given_the_environment_form_settings_stay_expressions(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        import os
        from pydantic_ai import Agent

        settings = Agent("test", end_strategy=os.getenv("END_STRATEGY", "early"))
        """,
    )
    blueprint = agent(found, "settings")
    assert blueprint.settings.end_strategy is None
    assert blueprint.settings.expressions == {"end_strategy": 'os.getenv("END_STRATEGY", "early")'}


# ------------------------------------------------------------ pure text functions

DEDENTED = """
    You are a careful assistant.
      Indented by two more.

    Answer briefly.
    """


def test_dedent_and_cleandoc_of_a_literal_give_what_python_gives(tmp_path: Path) -> None:
    lines = [
        "import inspect",
        "import textwrap",
        "import textwrap as tw",
        "from inspect import cleandoc as tidy",
        "from textwrap import dedent",
        "from pydantic_ai import Agent",
        f"PROMPT = {DEDENTED!r}",
        "agent = Agent(",
        '    "test",',
        "    instructions=[",
        "        dedent(PROMPT),",
        "        textwrap.dedent(PROMPT),",
        "        tw.dedent(PROMPT).strip(),",
        "        inspect.cleandoc(PROMPT),",
        "        tidy(PROMPT),",
        '        dedent("  first" + PROMPT),',
        "    ],",
        "    system_prompt=dedent(PROMPT),",
        '    description=dedent("  One line."),',
        ")",
    ]
    path = tmp_path / "module.py"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    found = analyze(path)
    blueprint = agent(found, "agent")
    assert [(i.dynamic, i.text, i.expression) for i in blueprint.instructions] == [
        (False, textwrap.dedent(DEDENTED), None),
        (False, textwrap.dedent(DEDENTED), None),
        (False, textwrap.dedent(DEDENTED).strip(), None),
        (False, inspect.cleandoc(DEDENTED), None),
        (False, inspect.cleandoc(DEDENTED), None),
        (False, textwrap.dedent("  first" + DEDENTED), None),
        (False, textwrap.dedent(DEDENTED), None),
    ]
    assert blueprint.description == "One line."
    assert blueprint.warnings == []


def test_dedent_of_anything_but_one_literal_string_stays_the_expression(tmp_path: Path) -> None:
    found = analyze_source(
        tmp_path,
        """
        from textwrap import dedent, indent
        from prompt_tools import cleandoc
        from pydantic_ai import Agent

        agent = Agent(
            "test",
            instructions=[
                dedent(load_prompt()),
                dedent(f"Today is {today()}."),
                dedent("a", "b"),
                dedent(text="  a"),
                dedent(*parts),
                dedent(3),
                indent("a", "  "),
                cleandoc("  not the standard library's"),
            ],
        )
        """,
    )
    blueprint = agent(found, "agent")
    assert [(i.dynamic, i.text) for i in blueprint.instructions] == [(True, None)] * 8
    assert [i.expression for i in blueprint.instructions] == [
        "dedent(load_prompt())",
        'dedent(f"Today is {today()}.")',
        'dedent("a", "b")',
        'dedent(text="  a")',
        "dedent(*parts)",
        "dedent(3)",
        'indent("a", "  ")',
        'cleandoc("  not the standard library\'s")',
    ]
    assert len(blueprint.warnings) == 8
