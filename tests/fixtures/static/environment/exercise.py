"""An exercise whose agents get their model from ``llm.py``."""

from __future__ import annotations

from llm import get_model, model_id, required_model
from pydantic_ai import Agent

tools_agent = Agent(get_model(), name="tools_agent", instructions="Call the tools instead of guessing.")
by_id = Agent(model_id(), name="by_id")
required = Agent(required_model(), name="required")
