"""Support triage demo: parallel tools, a retry, a delegated refund and structured output.

A customer writes in about a coffee maker that arrived cracked. The `triage` agent looks up
the order, the customer's ticket history and the damage policy at the same time; the order
lookup fails once because the id is malformed and is retried. Triage then hands the refund to
the `refunds` agent and closes the ticket with a structured `Resolution`.

Everything is scripted and offline: the "models" are `FunctionModel`s and the store is a dict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.toolsets import FunctionToolset

from ._base import Demo
from ._script import History, Turn, call, finish, pause, pause_sync, playback, scripted_model

TICKET = (
    "Hi, my order 1042 arrived this morning and the glass carafe of the coffee maker is cracked "
    "right down the side. I have not even used it. Can I get my money back?"
)


class Order(BaseModel):
    """An order as stored by the shop."""

    order_id: str
    customer_id: str
    item: str
    total_eur: float
    status: Literal["processing", "shipped", "delivered"]
    delivered_on: str | None
    days_since_delivery: int | None


class Ticket(BaseModel):
    """An earlier support ticket from the same customer."""

    ticket_id: str
    opened_on: str
    subject: str
    outcome: str


class Article(BaseModel):
    """A knowledge-base article."""

    article_id: str
    title: str
    body: str


class RefundReceipt(BaseModel):
    """Confirmation that a refund was sent to the payment provider."""

    refund_id: str
    order_id: str
    amount_eur: float
    method: str
    eta_business_days: int


class Resolution(BaseModel):
    """How the ticket was resolved."""

    status: Literal["refunded", "replaced", "escalated"]
    refund_eur: float = Field(description="Amount refunded to the customer, 0 if none.")
    reply: str = Field(description="The message sent to the customer.")


@dataclass
class SupportDeps:
    """Who the agent is talking to."""

    customer_id: str = "C-2087"
    customer_name: str = "Ann Okafor"
    tier: Literal["standard", "gold"] = "gold"
    customer_since: int = 2021


_ORDERS = {
    "A-1042": Order(
        order_id="A-1042",
        customer_id="C-2087",
        item="Aero pour-over coffee maker, 1 l glass carafe",
        total_eur=64.90,
        status="delivered",
        delivered_on="2026-03-02",
        days_since_delivery=0,
    ),
    "A-0977": Order(
        order_id="A-0977",
        customer_id="C-2087",
        item="Burr hand grinder",
        total_eur=48.00,
        status="delivered",
        delivered_on="2026-01-12",
        days_since_delivery=49,
    ),
}

_TICKETS = {
    "C-2087": [
        Ticket(
            ticket_id="T-5519",
            opened_on="2026-01-09",
            subject="Where is my grinder?",
            outcome="Carrier delay, delivered three days later. No compensation requested.",
        )
    ]
}

_ARTICLES = [
    Article(
        article_id="KB-12",
        title="Damaged on arrival",
        body=(
            "Items that arrive damaged are refunded in full when reported within 30 days of delivery. "
            "For orders under EUR 100 the customer keeps or recycles the item; no return and no photo is needed."
        ),
    ),
    Article(
        article_id="KB-31",
        title="Refund timing",
        body="Refunds go back to the original payment method and arrive within 3 business days.",
    ),
    Article(
        article_id="KB-07",
        title="Changing a delivery address",
        body="The address can be changed until the order status is 'shipped'.",
    ),
]

# --- refunds sub-agent --------------------------------------------------------------------


def _refund_confirmation(history: History) -> Turn:
    receipt: RefundReceipt = history.result("issue_refund")
    return Turn(
        text=(
            f"Refund {receipt.refund_id} for EUR {receipt.amount_eur:.2f} was issued to the customer's "
            f"{receipt.method} and will arrive within {receipt.eta_business_days} business days."
        )
    )


refunds = Agent(
    scripted_model(
        "scripted-support",
        Turn(calls=(call("issue_refund", order_id="A-1042"),)),
        _refund_confirmation,
    ),
    name="refunds",
    description="Issues refunds for orders that qualify",
    deps_type=SupportDeps,
    instructions="You issue refunds. Refund the full order total to the original payment method, then confirm.",
)


@refunds.tool
async def issue_refund(ctx: RunContext[SupportDeps], order_id: str) -> RefundReceipt:
    """Refund an order in full to the original payment method.

    Args:
        order_id: The order to refund, for example 'A-1042'.
    """
    await pause(700)
    order = _ORDERS[order_id]
    if order.customer_id != ctx.deps.customer_id:
        raise ModelRetry(f"Order {order_id} does not belong to this customer.")
    return RefundReceipt(
        refund_id="RF-7731", order_id=order_id, amount_eur=order.total_eur, method="card", eta_business_days=3
    )


# --- triage agent -------------------------------------------------------------------------

kb = FunctionToolset[SupportDeps](id="kb")


@kb.tool_plain
def search_kb(query: str) -> list[Article]:
    """Search the knowledge base.

    Args:
        query: Keywords to look for.
    """
    pause_sync(450)
    words = set(re.findall(r"[a-z]+", query.lower()))
    return [
        article
        for article in _ARTICLES
        if words & set(re.findall(r"[a-z]+", f"{article.title} {article.body}".lower()))
    ][:2]


def _triage_final(history: History) -> Turn:
    order: Order = history.result("get_order")
    reply = (
        "Hi Ann, I am sorry your coffee maker arrived cracked. I have refunded the full "
        f"EUR {order.total_eur:.2f} to your card; it should reach you within 3 business days. "
        "There is no need to send the damaged one back. Thank you for being with us since 2021."
    )
    return Turn(calls=(finish(status="refunded", refund_eur=order.total_eur, reply=reply),))


triage = Agent(
    scripted_model(
        "scripted-support",
        Turn(
            text=(
                "Sorry to hear that. Let me pull up the order, check for earlier tickets "
                "and look up our policy for items damaged in transit."
            ),
            calls=(
                call("get_order", order_id="1042"),
                call("recent_tickets"),
                call("search_kb", query="damaged on arrival refund"),
            ),
        ),
        Turn(text="The order id needs its 'A-' prefix. Trying again.", calls=(call("get_order", order_id="A-1042"),)),
        Turn(
            text=(
                "The order was delivered today, well inside the 30-day window, and it is under EUR 100, "
                "so it qualifies for a full refund with no return. Handing it to refunds."
            ),
            calls=(call("escalate_refund", order_id="A-1042", reason="Glass carafe cracked on arrival, unused."),),
        ),
        _triage_final,
    ),
    name="triage",
    description="Resolves customer support tickets",
    deps_type=SupportDeps,
    output_type=Resolution,
    instructions="You are a support triage agent for a kitchenware shop. Check the facts before you promise anything.",
    system_prompt="Be concise and kind.",
    toolsets=[kb],
    retries=3,
    model_settings={"temperature": 0.2},
)


@triage.instructions
def customer_context(ctx: RunContext[SupportDeps]) -> str:
    deps = ctx.deps
    return f"Customer: {deps.customer_name} ({deps.tier} tier, customer since {deps.customer_since})."


@triage.tool(retries=2)
async def get_order(ctx: RunContext[SupportDeps], order_id: str) -> Order:
    """Fetch an order by id.

    Args:
        order_id: The order id, for example 'A-1042'.
    """
    await pause(600)
    if not re.fullmatch(r"A-\d{4}", order_id):
        raise ModelRetry(f"Order ids look like 'A-1042', got {order_id!r}.")
    order = _ORDERS.get(order_id)
    if order is None or order.customer_id != ctx.deps.customer_id:
        raise ModelRetry(f"No order {order_id} for this customer.")
    return order


@triage.tool
async def recent_tickets(ctx: RunContext[SupportDeps]) -> list[Ticket]:
    """List this customer's earlier support tickets, newest first."""
    await pause(350)
    return _TICKETS.get(ctx.deps.customer_id, [])


@triage.tool
async def escalate_refund(ctx: RunContext[SupportDeps], order_id: str, reason: str) -> str:
    """Hand the refund to the refunds agent.

    Args:
        order_id: The order to refund.
        reason: Why the customer is being refunded.
    """
    result = await refunds.run(f"Refund order {order_id} in full. Reason: {reason}", deps=ctx.deps, usage=ctx.usage)
    return result.output


async def run(speed: float = 1.0) -> Resolution:
    """Play the scenario once and return the resolution."""
    with playback(speed):
        result = await triage.run(TICKET, deps=SupportDeps())
    return result.output


demo = Demo(
    name="support",
    title="Support triage",
    description="A triage agent resolves a damaged-order ticket: parallel tools, a retry, a delegated refund.",
    targets=(triage, refunds),
    run=run,
)
