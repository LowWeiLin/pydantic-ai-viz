"""Tool descriptions and parameter descriptions from a docstring.

Pydantic AI builds a tool's description from its docstring with griffe, which it
depends on. The same library is used here, on the docstring text alone, so the
result matches what the model is sent for Google, NumPy and Sphinx styles. If
griffe is missing or its API has moved, the whole docstring becomes the
description and no parameter is described; nothing fails.
"""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Generator
from contextlib import contextmanager
from importlib import import_module
from typing import Any, Literal, TypeAlias

Style: TypeAlias = Literal["google", "numpy", "sphinx"]

_SPHINX = re.compile(
    r"\n[ \t]*:(param|parameter|arg|argument|key|keyword|type|var|ivar|cvar|vartype|returns|return|rtype|raises"
    r"|raise|except|exception)([ \t]+\w+)*:([ \t]+.+)?\n",
    re.IGNORECASE,
)
_GOOGLE = re.compile(
    r"\n[ \t]*(args|arguments|params|parameters|keyword args|keyword arguments|other args|other arguments"
    r"|other params|other parameters|raises|exceptions|returns|yields|receives|examples|attributes|functions"
    r"|methods|classes|modules|warns|warnings):([ \t]+.+)?\n[ \t]+.+",
    re.IGNORECASE,
)
_NUMPY = re.compile(
    r"\n[ \t]*(deprecated|parameters|other parameters|returns|yields|receives|raises|warns|attributes|functions"
    r"|methods|classes|modules)\n[ \t]*---+\n",
    re.IGNORECASE,
)


def infer_style(doc: str) -> Style:
    """Which docstring convention ``doc`` follows. The same simple test Pydantic AI applies."""
    if _SPHINX.search(doc):
        return "sphinx"
    if _GOOGLE.search(doc):
        return "google"
    if _NUMPY.search(doc):
        return "numpy"
    return "google"


@contextmanager
def _quiet_griffe() -> Generator[None]:
    """Griffe logs a warning for every docstring it cannot fully parse; that is the author's business."""
    logger = logging.getLogger("griffe")
    was_disabled = logger.disabled
    logger.disabled = True
    try:
        yield
    finally:
        logger.disabled = was_disabled


def _sections(doc: str, style: Style) -> list[Any]:
    griffe: Any = import_module("griffe")
    options: Any = None
    if style == "google":
        factory: Any = getattr(griffe, "GoogleOptions", None)
        if factory is not None:
            options = factory(returns_named_value=False, returns_multiple_items=False, warnings=False)
    else:
        factory = getattr(griffe, "NumpyOptions" if style == "numpy" else "SphinxOptions", None)
        if factory is not None:
            options = factory(warnings=False)
    with _quiet_griffe():
        if options is not None:
            try:
                return list(griffe.Docstring(doc, lineno=1, parser=style, parser_options=options).parse())
            except TypeError:
                pass  # an older griffe without ``parser_options``
        return list(griffe.Docstring(doc, lineno=1, parser=style).parse())


def _kind(section: Any) -> str:
    kind: Any = getattr(section, "kind", None)
    return str(getattr(kind, "value", kind))


def describe(
    doc: str | None, *, has_return_annotation: bool, style: Style | None = None
) -> tuple[str | None, dict[str, str]]:
    """The description of a function and of its parameters, from its docstring.

    Args:
        doc: The docstring as written, or ``None`` when there is none.
        has_return_annotation: Whether the function declares its return type. With a
            declared type, the text of a ``Returns:`` section is all description.
        style: The convention to read the docstring by, as a tool's ``docstring_format`` names
            it; ``None`` infers it from the docstring, as ``"auto"`` does.

    Returns:
        The main description (``None`` without a docstring) and a mapping of
        parameter name to description. When the docstring documents the return
        value, the description uses the ``<summary>``/``<returns>`` form that
        Pydantic AI sends to the model.
    """
    if doc is None:
        return None, {}
    cleaned = inspect.cleandoc(doc)
    try:
        style = style or infer_style(doc)
        sections = _sections(doc, style)
        return _from_sections(sections, style, has_return_annotation=has_return_annotation)
    except Exception:
        return cleaned or None, {}


def _from_sections(
    sections: list[Any], style: Style, *, has_return_annotation: bool
) -> tuple[str | None, dict[str, str]]:
    parameters: dict[str, str] = {}
    main: list[str] = []
    returns: Any = None
    for section in sections:
        kind = _kind(section)
        if kind in ("parameters", "other parameters"):
            for parameter in section.value:
                parameters.setdefault(str(parameter.name).lstrip("*"), str(parameter.description))
        elif kind == "text":
            main.append(str(section.value))
        elif kind == "admonition":
            title = str(getattr(section, "title", None) or getattr(section.value, "kind", "")).strip()
            main.append(f"{title}:\n{section.value.contents}" if title else str(section.value.contents))
        elif kind == "returns" and returns is None and section.value:
            returns = section.value[0]
    description = "\n\n".join(main)
    if returns is not None:
        return_description = str(returns.description)
        annotation: Any = getattr(returns, "annotation", None)
        type_tag = ""
        if annotation:
            if style == "google" and has_return_annotation:
                return_description = f"{annotation}: {return_description}"
            else:
                type_tag = f"<type>{annotation}</type>\n"
        block = f"<returns>\n{type_tag}<description>{return_description}</description>\n</returns>"
        description = f"<summary>{description}</summary>\n{block}" if description else block
    return description or None, parameters
