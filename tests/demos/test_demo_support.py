from __future__ import annotations

from collections import Counter
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolReturnPart

from pydantic_ai_viz.demos import support
from pydantic_ai_viz.demos.support import Resolution, SupportDeps, refunds, triage

from .conftest import Spy, tool_calls, transcript

pytestmark = pytest.mark.anyio

RESOLUTION = Resolution(
    status="refunded",
    refund_eur=64.9,
    reply=(
        "Hi Ann, I am sorry your coffee maker arrived cracked. I have refunded the full EUR 64.90 to your card; "
        "it should reach you within 3 business days. There is no need to send the damaged one back. "
        "Thank you for being with us since 2021."
    ),
)

EXPECTED_CALLS = [
    [
        ("get_order", {"order_id": "1042"}),
        ("recent_tickets", {}),
        ("search_kb", {"query": "damaged on arrival refund"}),
    ],
    [("get_order", {"order_id": "A-1042"})],
    [("escalate_refund", {"order_id": "A-1042", "reason": "Glass carafe cracked on arrival, unused."})],
    [("final_result", RESOLUTION.model_dump())],
]


@pytest.mark.parametrize("stream", [False, True], ids=["plain", "streamed"])
async def test_scenario(spy: Spy, stream: bool) -> None:
    runs = spy(triage, refunds, stream=stream)
    result = await triage.run(support.TICKET, deps=SupportDeps())
    messages = result.all_messages()

    assert result.output == RESOLUTION
    assert tool_calls(messages) == EXPECTED_CALLS

    # Three parallel tool calls in the first turn, after some text.
    first = messages[1]
    assert isinstance(first, ModelResponse)
    assert isinstance(first.parts[0], TextPart)
    assert len(tool_calls(messages)[0]) == 3

    # Exactly one ModelRetry, on the malformed order id, and it recovers.
    returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
    assert [(part.tool_name, part.content) for part in returns if part.outcome == "retried"] == [
        ("get_order", "Order ids look like 'A-1042', got '1042'.")
    ]
    assert [part.tool_name for part in returns if part.outcome == "success"] == [
        "recent_tickets",
        "search_kb",
        "get_order",
        "escalate_refund",
        "final_result",
    ]
    assert {part.outcome for part in returns} == {"retried", "success"}

    # One delegation, whose requests are counted in the parent's usage (usage=ctx.usage).
    assert runs == Counter(triage=1, refunds=1)
    assert result.usage.requests == 4 + 2


async def test_tool_results() -> None:
    result = await triage.run(support.TICKET, deps=SupportDeps())
    by_tool: dict[str, Any] = {
        part.tool_name: part.content
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.outcome == "success"
    }
    assert by_tool["get_order"].total_eur == 64.90
    assert [ticket.ticket_id for ticket in by_tool["recent_tickets"]] == ["T-5519"]
    assert [article.article_id for article in by_tool["search_kb"]] == ["KB-12", "KB-31"]
    assert by_tool["escalate_refund"] == (
        "Refund RF-7731 for EUR 64.90 was issued to the customer's card and will arrive within 3 business days."
    )


async def test_refunds_agent_refuses_another_customers_order() -> None:
    with pytest.raises(Exception, match="issue_refund"):
        await refunds.run("Refund order A-1042 in full.", deps=SupportDeps(customer_id="C-0001"))


async def test_deterministic_plain_and_streamed(spy: Spy) -> None:
    first = await triage.run(support.TICKET, deps=SupportDeps())
    second = await triage.run(support.TICKET, deps=SupportDeps())
    spy(triage, refunds, stream=True)
    streamed = await triage.run(support.TICKET, deps=SupportDeps())
    assert transcript(first.all_messages()) == transcript(second.all_messages())
    assert transcript(first.all_messages()) == transcript(streamed.all_messages())
    assert first.output == second.output == streamed.output


async def test_demo_run_returns_the_resolution() -> None:
    assert await support.demo.run(0) == RESOLUTION
    assert await support.demo.run(0) == RESOLUTION


def test_blueprint_material() -> None:
    """The static features the demo is meant to show are really on the agent."""
    assert triage.deps_type is SupportDeps
    assert triage.output_type is Resolution
    assert triage.model_settings == {"temperature": 0.2}
    assert support.kb.id == "kb"
    assert list(support.kb.tools) == ["search_kb"]
