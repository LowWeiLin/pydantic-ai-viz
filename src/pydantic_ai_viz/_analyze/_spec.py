"""Agents made from a spec: ``Agent.from_file("agent.yaml")`` and ``Agent.from_spec({...})``.

A spec is data, so it is read, never run: a file with ``json.loads`` or, when PyYAML is installed,
``yaml.safe_load``. What it says is written out as the ``Agent(...)`` call that ``from_spec`` makes of it,
in Python, and that call is read like any other: its capabilities are classes Pydantic AI looks up by name,
and that is what the source written out imports.
"""

from __future__ import annotations

import json
import keyword
import math
from pathlib import Path
from typing import Any, cast

import pydantic_ai

from ._source import Module, SkippedFile, parse_module

SPEC_METHODS = frozenset({"from_spec", "from_file"})
"""The class methods of ``Agent`` that make an agent from a spec."""
FORMATS: dict[str, str] = {".json": "json", ".yaml": "yaml", ".yml": "yaml"}
"""The format ``Agent.from_file`` reads a file in, by its suffix, unless it is given ``fmt=``."""
_REPLACED = ("model", "name", "description", "end_strategy", "tool_timeout")
"""Fields of a spec that the keyword of the same name given to the call replaces."""
_UNKNOWN = "__spec__"
"""Where the source written out imports a capability from that is not known: a module no project has."""


def registry() -> dict[str, str]:
    """The capabilities a spec may name without ``custom_capability_types``: name -> the dotted name of the class.

    Those of the installed Pydantic AI, which imports them itself; none in a release without specs.
    """
    types: dict[str, Any] = getattr(getattr(pydantic_ai, "capabilities", None), "CAPABILITY_TYPES", {})
    return {str(name): f"pydantic_ai.capabilities.{name}" for name in types}


def spec_target(path: Path, rel: str, *, max_bytes: int) -> Module | SkippedFile:
    """An agent spec named as a target, as the source ``agent = Agent.from_file("<its name>")`` it stands for."""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size > max_bytes:
        return SkippedFile(rel, f"it is larger than the {max_bytes:,} byte limit ({size:,} bytes)")
    spec, warning = load_spec(path, FORMATS[path.suffix.lower()])
    # A YAML file that cannot be read without PyYAML is taken as a spec; its agent says why it is not read.
    if spec is not None and not {"model", "capabilities", "instructions", "$schema"} & set(spec):
        return SkippedFile(rel, "it is not an agent spec")
    if spec is None and warning is not None and "PyYAML" not in warning:
        return SkippedFile(rel, "it " + warning.removeprefix(f"the agent spec `{path.name}` "))
    written = f"from pydantic_ai import Agent; agent = Agent.from_file({path.name!r})\n"
    return parse_module(written.encode("utf-8"), path, rel)


def load_spec(path: Path, fmt: str) -> tuple[dict[str, Any] | None, str | None]:
    """The spec in a file, or a warning that says why it cannot be read."""
    name = path.name
    try:
        text = path.read_bytes().decode("utf-8").removeprefix("\ufeff")
    except (OSError, UnicodeDecodeError):
        return None, f"the agent spec `{name}` cannot be read"
    if fmt == "json":
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            return None, f"the agent spec `{name}` is not valid JSON"
    else:
        try:
            import yaml
        except ImportError:
            return None, f"`{name}` is YAML; install PyYAML to read it"
        try:
            data = yaml.safe_load(text)
        except (yaml.YAMLError, RecursionError):
            return None, f"the agent spec `{name}` is not valid YAML"
    if not isinstance(data, dict):
        return None, f"the agent spec `{name}` is not an object"
    return cast("dict[str, Any]", data), None


def spec_source(spec: dict[str, Any], classes: dict[str, str], line: int) -> tuple[str, list[str]]:
    """The ``Agent(...)`` call ``from_spec`` makes of ``spec``, as Python on line ``line``, and warnings.

    ``classes`` maps the capability names the spec may use to the dotted names of their classes.
    """
    warnings: list[str] = []
    imports: dict[str, list[str]] = {}
    arguments: list[str] = []

    def add(key: str, value: Any) -> None:
        try:
            arguments.append(f"{key}={_python(value)}")
        except (ValueError, RecursionError):
            warnings.append(f"the `{key}` of the agent spec cannot be read")

    for key in _REPLACED:
        if spec.get(key) is not None:
            add(key, spec[key])
    if spec.get("instructions") is not None:
        given = spec["instructions"]
        add("instructions", given if isinstance(given, list) else [given])
    for key in ("model_settings", "retries"):
        if spec.get(key) is not None:
            add(key, spec[key])
    if isinstance(spec.get("output_schema"), dict):
        try:
            arguments.append(f"output_type=StructuredDict({_python(spec['output_schema'])})")
            imports.setdefault("pydantic_ai", []).append("StructuredDict")
        except (ValueError, RecursionError):
            warnings.append("the `output_schema` of the agent spec cannot be read")
    capabilities: list[str] = []
    given = spec.get("capabilities")
    for entry in cast("list[Any]", given) if isinstance(given, list) else []:
        made = _capability(entry, classes, imports, warnings)
        if made is not None:
            capabilities.append(made)
    if capabilities:
        arguments.append(f"capabilities=[{', '.join(capabilities)}]")
    statements = [f"from {module} import {', '.join(dict.fromkeys(names))}" for module, names in imports.items()]
    statements.append(f"Agent({', '.join(arguments)})")
    return "\n" * (line - 1) + "; ".join(statements) + "\n", warnings


def _capability(entry: Any, classes: dict[str, str], imports: dict[str, list[str]], warnings: list[str]) -> str | None:
    """One capability of a spec as the call that makes it: ``'Name'``, ``{Name: arguments}`` or the long form."""
    fields = cast("dict[Any, Any]", entry) if isinstance(entry, dict) else {}
    name: Any = entry if isinstance(entry, str) else None
    arguments: Any = None
    if len(fields) == 1:
        ((name, arguments),) = fields.items()
        if not (isinstance(arguments, dict) and all(isinstance(key, str) for key in cast("dict[Any, Any]", arguments))):
            arguments = (arguments,)  # anything but keyword arguments is one positional argument
    elif set(fields) == {"name", "arguments"}:
        name, arguments = fields["name"], fields["arguments"]
        if isinstance(arguments, list):
            arguments = tuple(cast("list[Any]", arguments))
        elif arguments is not None and not isinstance(arguments, dict):
            name = None
    if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name):
        warnings.append("a capability of the agent spec cannot be read")
        return None
    dotted = classes.get(name)
    if dotted is None:
        warnings.append(
            f"the agent spec names the capability `{name}`, which Pydantic AI does not know unless its class is "
            "given as `custom_capability_types`"
        )
    imports.setdefault(_UNKNOWN if dotted is None else dotted.rpartition(".")[0], []).append(name)
    try:
        if isinstance(arguments, dict):
            given = cast("dict[str, Any]", arguments)
            if not all(key.isidentifier() and not keyword.iskeyword(key) for key in given):
                raise ValueError
            written = [f"{key}={_python(value)}" for key, value in given.items()]
        else:
            written = [_python(value) for value in cast("tuple[Any, ...]", arguments or ())]
    except (ValueError, RecursionError):
        warnings.append(f"the arguments of capability `{name}` in the agent spec cannot be read")
        written = []
    return f"{name}({', '.join(written)})"


def _python(value: Any) -> str:
    """``value`` as a Python literal; ``ValueError`` for what JSON and YAML hold that one cannot be written for."""
    if value is None or isinstance(value, bool | str | int):
        return repr(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_python(item) for item in cast("list[Any]", value)) + "]"
    if isinstance(value, dict):
        items: list[tuple[Any, Any]] = list(cast("dict[Any, Any]", value).items())
        if all(isinstance(key, str) for key, _ in items):
            return "{" + ", ".join(f"{key!r}: {_python(item)}" for key, item in items) + "}"
    raise ValueError("not a JSON value")
