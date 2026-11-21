"""The skills a harness ``Skills(...)`` offers, read from the ``SKILL.md`` files of the analysed directories.

Only files inside the analysed directories are read, and nothing is run: the frontmatter is read by a small
parser of its own, which takes what Agent Skills use (``key: value`` lines, quoted values, folded lines and
``|``/``>`` blocks), not all of YAML. A skill is offered as the harness offers it: an immediate child of a library
directory that holds a ``SKILL.md``, in the order of their names.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .._models import SkillInfo
from .._normalize import clip

MAX_SKILLS = 64
"""Up to how many skills of one ``Skills`` are shown."""

_BLOCKS = frozenset({"|", ">", "|-", ">-", "|+", ">+"})
_COMMENT = re.compile(r"(?:^|\s)#")
"""Where a comment starts in a value: a ``#`` at its start or after a space."""
_MAPPING = re.compile(r":(?:\s|$)")
"""A ``:`` that YAML reads as starting a mapping, which a plain value cannot hold."""
_INDICATORS = frozenset("[]{},&*!|>%@`")
"""Characters a plain YAML value cannot start with."""
_READ = frozenset({"name", "description", "when_to_use", "disable-model-invocation", "user-invocable"})
"""The fields of the frontmatter that are read, which have to be text."""


@dataclass(slots=True)
class Catalog:
    """What the libraries of one ``Skills`` offer, and warnings about what was left out."""

    skills: list[SkillInfo] = field(default_factory=list[SkillInfo])
    warnings: list[str] = field(default_factory=list[str])


def contained(path: Path, bases: list[Path]) -> Path | None:
    """``path`` with its links resolved, when that lies inside one of ``bases``; ``None`` otherwise."""
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return None
    return resolved if any(resolved.is_relative_to(base) for base in bases) else None


def library(root: Path, directory: str, bases: list[Path]) -> Path | None:
    """The library ``directory`` names, relative to ``root`` unless absolute, when it is an analysed directory.

    ``~`` is not expanded, as the harness does not expand it.
    """
    found = contained(root / directory, bases)
    return found if found is not None and found.is_dir() else None


def read_catalog(
    directories: list[str],
    root: Path,
    *,
    bases: list[Path],
    shown_from: Path,
    include: list[str] | None,
    exclude: list[str],
    max_file_bytes: int,
) -> Catalog:
    """The skills of the libraries ``directories``, read relative to ``root``.

    ``bases`` are the analysed directories, the only ones read from; paths are shown relative to ``shown_from``.
    """
    catalog = Catalog()
    selected = None if include is None else {_normalized(name) for name in include}
    left_out = {_normalized(name) for name in exclude}
    seen: set[str] = set()
    for directory in directories:
        found = library(root, directory, bases)
        if found is None:
            catalog.warnings.append(
                f"skill library `{directory}` was not found in the analysed files, so its skills are not shown"
            )
            continue
        if (found / "SKILL.md").is_file():
            catalog.warnings.append(
                f"skill `{shown(found, shown_from)}` was left out: the library path is not a library"
            )
            continue
        for child in sorted(found.iterdir(), key=lambda item: item.name):
            name = _normalized(child.name)
            skill_file = child / "SKILL.md"
            if not child.is_dir() or not skill_file.is_file():
                continue
            if (selected is not None and name not in selected) or name in left_out:
                continue
            rel = shown(skill_file, shown_from)
            if name in seen:
                catalog.warnings.append(f"skill `{rel}` was left out: another skill has its name")
                continue
            if _size(skill_file, bases) > max_file_bytes:
                catalog.warnings.append(
                    f"skill `{rel}` is not shown: its SKILL.md is larger than the {max_file_bytes} byte limit of the "
                    "analysis; a run still offers it"
                )
                continue
            skill = _read(skill_file, name, rel, bases)
            if skill is None:
                catalog.warnings.append(f"skill `{rel}` was left out: its SKILL.md could not be read")
                continue
            seen.add(name)
            if len(catalog.skills) == MAX_SKILLS:
                catalog.warnings.append(f"only the first {MAX_SKILLS} skills of `Skills` are shown")
                return catalog
            catalog.skills.append(skill)
    return catalog


def _normalized(name: str) -> str:
    return unicodedata.normalize("NFKC", name)


def shown(path: Path, shown_from: Path) -> str:
    """``path`` relative to ``shown_from``, with forward slashes; never absolute."""
    try:
        return os.path.relpath(path, shown_from).replace(os.sep, "/")
    except ValueError:  # another drive
        return "/".join(path.parts[-2:])


def _size(skill_file: Path, bases: list[Path]) -> int:
    """The size of a ``SKILL.md`` inside the analysed directories; 0 for one outside them or not there."""
    found = contained(skill_file, bases)
    try:
        return 0 if found is None else found.stat().st_size
    except OSError:
        return 0


def _read(skill_file: Path, name: str, rel: str, bases: list[Path]) -> SkillInfo | None:
    """One skill, or ``None`` when its ``SKILL.md`` is not one the harness would take."""
    found = contained(skill_file, bases)
    if found is None:
        return None
    try:
        text = found.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    parsed = frontmatter(text)
    if parsed is None:
        return None
    fields, body = parsed
    description = fields.get("description", "").strip()
    given = fields.get("name")
    flags = {
        key: fields.get(key, "false" if key == "disable-model-invocation" else "true").strip().lower()
        for key in ("disable-model-invocation", "user-invocable")
    }
    if (
        not description
        or (given is not None and _normalized(given) != name)
        or not _valid_name(name)
        or any(value not in ("true", "false") for value in flags.values())
    ):
        return None
    when_to_use = fields.get("when_to_use", "").strip() or None
    return SkillInfo(
        name=clip(name),
        description=clip(description),
        when_to_use=clip(when_to_use),
        model_invocable=flags["disable-model-invocation"] != "true",
        path=clip(rel),
        body=clip(body),
    )


def _valid_name(name: str) -> bool:
    """Whether the harness takes ``name`` for a skill: lowercase letters, digits and single inner hyphens."""
    return (
        0 < len(name) <= 64
        and name == name.lower()
        and not name.startswith("-")
        and not name.endswith("-")
        and "--" not in name
        and all(character.isalnum() or character == "-" for character in name)
    )


def frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    """The top-level fields of the frontmatter of a ``SKILL.md``, and its body; ``None`` when it has none."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line == "---"), None)
    if closing is None:
        return None
    fields: dict[str, str] = {}  # a plain or quoted value as written, its lines joined, until the end
    blocks: set[str] = set()
    opened: set[str] = set()
    key: str | None = None
    block: str | None = None
    held: list[str] = []

    def done() -> None:
        if key is not None and block is not None:
            fields[key] = _block(held, block)

    for line in lines[1:closing]:
        if line[:1] in (" ", "\t") or not line.strip():
            if key is None:
                if line.strip():
                    return None  # an indented line before any key
                continue
            if block is not None:
                held.append(line)
            elif line.strip():
                fields[key] = f"{fields[key]} {line.strip()}".strip()
            continue
        if line.startswith("#"):
            continue
        done()
        name, colon, value = line.partition(":")
        if not colon or not name.strip() or name.strip() in fields or value[:1] not in ("", " ", "\t"):
            return None
        key, value = name.strip(), value.strip()
        style = _COMMENT.split(value, maxsplit=1)[0].strip()
        block, held = (style, []) if style in _BLOCKS else (None, [])
        fields[key] = value
        if block is not None:
            blocks.add(key)
        if not value:
            opened.add(key)  # what it holds is on the lines below
    done()
    for name, value in list(fields.items()):
        if name not in blocks:
            scalar = _scalar(value)
            if scalar is None and name in opened and name not in _READ:
                del fields[name]  # a mapping or list under a key the harness does not read, as `metadata:`
            elif scalar is None:
                return None  # not YAML, or not text: the harness does not take it either
            else:
                fields[name] = scalar
    body = lines[closing + 1 :]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return fields, "\n".join(body)


def _scalar(value: str) -> str | None:
    """The text of a plain or quoted YAML value on one line; ``None`` when YAML does not read it as one."""
    if value[:1] in ("'", '"'):
        quote, start = value[0], 1
        while True:
            end = value.find(quote, start)
            if end < 0:
                return None  # never closed
            if quote == "'" and value[end + 1 : end + 2] == "'":
                start = end + 2  # '' is a quote inside
            elif quote == '"' and (end - len(value[:end].rstrip("\\"))) % 2:
                start = end + 1  # an escaped quote
            else:
                break
        rest = value[end + 1 :].strip()
        if rest and not rest.startswith("#"):
            return None  # text after the closing quote
        inner = value[1:end]
        return inner.replace("''", "'") if quote == "'" else inner.replace('\\"', '"').replace("\\\\", "\\")
    plain = _COMMENT.split(value, maxsplit=1)[0].strip()
    if plain[:1] in _INDICATORS or plain[:2] in ("- ", "? ") or _MAPPING.search(plain):
        return None  # a flow collection, an alias, a tag... or a mapping where a value goes
    return plain


def _block(lines: list[str], style: str) -> str:
    """The text of a ``|`` (lines kept) or ``>`` (lines folded) block."""
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    indent = min(indents, default=0)
    kept = [line[indent:] for line in lines]
    if style.startswith("|"):
        return "\n".join(kept)
    folded: list[str] = []
    for line in kept:
        if not line.strip():
            folded.append("\n")
        elif folded and not folded[-1].endswith("\n"):
            folded.append(f" {line}")
        else:
            folded.append(line)
    return "".join(folded)
