"""Everything spelled out: the analyser should read this file as completely as a live import would.

Runnable: the parity test imports it and compares the live blueprint with the static one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel

BASE_PROMPT = "You look after orders."


@dataclass
class ShopDeps:
    """What the tools need."""

    customer_id: str
    tier: Literal["standard", "gold"] = "standard"


class Verdict(BaseModel):
    """What the agent decided."""

    status: Literal["refunded", "replaced", "escalated"]
    refund_eur: float = Field(description="Amount refunded, 0 if none.", ge=0)
    notes: list[str] = Field(default_factory=list)
    reviewer: str | None = None


shop = Agent(
    TestModel(),
    name="shop",
    description="Resolves order problems",
    deps_type=ShopDeps,
    output_type=Verdict,
    instructions=(
        "Check the order before you promise anything. "
        "Refund only what the policy allows."
    ),
    system_prompt=[BASE_PROMPT, "Be brief."],
    retries={"tools": 3, "output": 2},
    end_strategy="exhaustive",
    model_settings={"temperature": 0.1, "max_tokens": 400},
    tool_timeout=12.5,
)


@shop.instructions
def who_is_asking(ctx: RunContext[ShopDeps]) -> str:
    return f"Customer {ctx.deps.customer_id} ({ctx.deps.tier})."


@shop.system_prompt
async def house_rules() -> str:
    return "Never share another customer's data."


@shop.tool
async def get_order(ctx: RunContext[ShopDeps], order_id: str, include_items: bool = False) -> dict[str, str]:
    """Fetch an order.

    Args:
        order_id: The order id, for example 'A-1042'.
        include_items: Also list the items.
    """
    return {"order_id": order_id, "customer": ctx.deps.customer_id}


@shop.tool_plain(retries=4)
def shipping_cost(weight_kg: float, express: bool = False, zone: Literal["eu", "world"] = "eu") -> float:
    """Work out what shipping costs.

    Parameters
    ----------
    weight_kg : float
        Parcel weight in kilograms.
    express : bool
        Next-day delivery.
    zone : str
        Where it goes.
    """
    return weight_kg * (2 if express else 1) * (3 if zone == "world" else 1)


@shop.tool_plain(name="lookup_policy", description="Find the policy text for a topic.")
def policy(topic: str, limit: Annotated[int, Field(description="How many paragraphs.", ge=1, le=5)] = 2) -> str:
    """This docstring is overridden by the decorator.

    :param topic: What the policy is about.
    """
    return f"{topic}: {limit}"


@shop.tool(requires_approval=True)
def issue_refund(ctx: RunContext[ShopDeps], order_id: str, amount_eur: float) -> str:
    """Refund an order.

    Args:
        order_id: The order to refund.
        amount_eur: How much to refund.

    Returns:
        A confirmation line.
    """
    return f"refunded {amount_eur} for {order_id}"


@shop.tool_plain
def ping() -> str:
    return "pong"
