"""The registry that puts each capability class in its group of the Pydantic AI capabilities docs."""

from __future__ import annotations

from typing import get_args

import pytest

from pydantic_ai_viz._capabilities import _GROUPS, category_of, is_secret, package_of, without_credentials
from pydantic_ai_viz._models import CapabilityCategory


def test_a_class_is_placed_by_its_package_and_its_name() -> None:
    assert category_of("pydantic_ai.capabilities.thinking", "Thinking") == "reasoning"
    assert category_of("pydantic_ai_harness.code_mode._capability", "CodeMode") == "context"
    # A class of the project that has the name of one of the harness's is not taken for it.
    assert category_of("myapp.memory", "Memory") is None
    assert category_of("pydantic_ai_harness.new_thing", "NewThing") is None


def test_classes_are_in_the_group_the_harness_readme_puts_them_in() -> None:
    assert category_of("pydantic_ai_harness.peers", "Peers") == "reasoning"
    assert category_of("pydantic_ai_harness.stale_files", "ReportStaleFiles") == "knowledge"
    assert category_of("pydantic_ai_harness.router", "ModelRouter") == "runtime"
    assert category_of("pydantic_ai_harness.coder._capability", "BenchmarkInstructions") == "harness"
    assert category_of("pydantic_ai.durable_exec._base", "BaseDurabilityCapability") == "runtime"


def test_deprecated_names_of_the_harness_are_placed_with_the_classes_they_name() -> None:
    assert category_of("pydantic_ai_harness.guardrails", "InputGuard") == "safety"
    assert category_of("pydantic_ai_harness.guardrails", "OutputGuard") == "safety"
    assert category_of("pydantic_ai_harness.bubblewrap_sandbox", "BubblewrapSandbox") == "environment"


def test_the_package_is_the_top_module() -> None:
    assert package_of("pydantic_ai.capabilities.MCP") == "pydantic_ai"
    assert package_of("pydantic_ai_harness") == "pydantic_ai_harness"
    assert package_of("pydantic_ai_slim.capabilities") is None
    assert package_of("myapp.capabilities") is None


def test_every_group_of_the_docs_has_classes_and_each_class_is_in_one_group() -> None:
    assert list(_GROUPS) == [category for category in get_args(CapabilityCategory) if category != "custom"]
    assert all(_GROUPS.values())
    names = [name for group in _GROUPS.values() for name in group]
    assert len(names) == len(set(names))


@pytest.mark.parametrize(
    ("key", "secret"),
    [
        ("authorization_token", True),
        ("auth_token", True),
        ("access_token", True),
        ("accessToken", True),
        ("token", True),
        ("bearer", True),
        ("authorization", True),
        ("auth", True),
        ("headers", True),
        ("http_headers", True),
        ("env", True),
        ("api_key", True),
        ("apikey", True),
        ("X-Api-Key", True),
        ("client_secret", True),
        ("password", True),
        ("passwd", True),
        ("credentials", True),
        ("cookies", True),
        ("url", False),
        ("effort", False),
        ("models", False),
        ("token_threshold", False),
        ("max_tokens", False),
        ("keep_tokens", False),
        ("target_tokens", False),
        ("min_prefix_tokens", False),
        ("authority", False),
        ("path_authority", False),
        ("environment", False),
        ("envelope", False),
        ("monkey", False),
    ],
)
def test_parameters_named_for_a_secret_are_secret(key: str, secret: bool) -> None:
    assert is_secret(key) is secret


def test_a_url_loses_what_may_hold_a_key() -> None:
    assert without_credentials("https://u:p@h.example/mcp?api_key=x#f") == "https://h.example/mcp"
    assert without_credentials("wss://h.example:8443/ws?token=x") == "wss://h.example:8443/ws"
    assert without_credentials("high") == "high"
    assert without_credentials("file:///tmp/x?y") == "file:///tmp/x?y"
    assert without_credentials("http://[broken") == "http://[broken"
