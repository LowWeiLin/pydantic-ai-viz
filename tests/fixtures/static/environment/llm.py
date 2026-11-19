"""Shared model wiring: the model id comes from the environment, with a default in the source.

The way a course or a small project often keeps its provider plumbing in one module.
Nothing here can be run: ``dotenv`` is not installed.
"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv  # not installed
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

load_dotenv()

DEFAULT_BASE_URL = "https://api.example.com/v1"
DEFAULT_MODEL = "acme-pass/fast-model-1017"


def model_id() -> str:
    """The model id in use."""
    return os.getenv("ACME_MODEL", DEFAULT_MODEL)


@lru_cache(maxsize=1)
def get_model() -> OpenAIChatModel:
    api_key = os.getenv("ACME_API_KEY")
    if not api_key:
        raise RuntimeError("ACME_API_KEY is not set")
    provider = OpenAIProvider(base_url=os.getenv("ACME_BASE_URL", DEFAULT_BASE_URL), api_key=api_key)
    return OpenAIChatModel(model_id(), provider=provider)


def required_model() -> OpenAIChatModel:
    return OpenAIChatModel(model_name=os.environ["ACME_REQUIRED_MODEL"])
