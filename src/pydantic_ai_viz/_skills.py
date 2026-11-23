"""The skills a harness ``Skills`` capability offers, read from the live object as a run reads them.

``Skills`` reads its libraries at the start of every run, so the agent never holds them. Here they are read with
the capability's public ``load()``, from host workspaces only: a directory on this machine, given as
``workspace=`` or by the agent's ``LocalWorkspace``. Reading never writes, and no absolute path reaches the wire.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from . import _compat
from ._models import SkillInfo
from ._normalize import clip

_T = TypeVar("_T")

LOAD_TIMEOUT_SECONDS = 5.0
"""How long reading the skills may take when a running event loop has them read in a thread of their own."""

_SKIPPED: dict[str, str] = {
    "invalid": "its SKILL.md could not be read",
    "duplicate_name": "another skill has its name",
    "not_a_directory": "the library path is not a library",
    "skill_package": "the library path is not a library",
    "flagged": "an artifact scan flagged it",
}
"""Why ``Skills`` left a path out (``SkippedSkill.reason``), in words."""


def is_skills(capability: object) -> bool:
    """Whether ``capability`` is the harness ``Skills``, or a class derived from it."""
    return any(
        base.__name__ == "Skills" and base.__module__.startswith(_compat.HARNESS_SKILLS_MODULE)
        for base in type(capability).__mro__
    )


def skill_catalog(capability: Any, capabilities: list[Any], warnings: list[str]) -> list[SkillInfo]:
    """The skills ``capability`` offers, in catalog order; ``capabilities`` are the agent's, for its workspace."""
    try:
        from pydantic_ai.workspaces import LocalWorkspaceBackend, WindowsWorkspaceBackend
    except ImportError:  # a release without workspaces has no harness that reads skills from one
        return []
    host = (LocalWorkspaceBackend, WindowsWorkspaceBackend)
    sources: Any = _compat.get_private(capability, _compat.SKILLS_SOURCES, (), warnings, "the skills of `Skills`")
    backends = [_innermost(getattr(source, "workspace", None)) for source in sources]
    for backend in backends:
        if backend is not None and not isinstance(backend, host):
            _compat.add_warning(
                warnings, f"`Skills` reads its skills from a {type(backend).__name__} workspace, which is not read"
            )
            return []
    run: Any = None
    if any(backend is None for backend in backends):
        run = _run_workspace(capabilities, LocalWorkspaceBackend, WindowsWorkspaceBackend)
        if run is None:
            _compat.add_warning(
                warnings, "the skills of `Skills` are read from the run's workspace, which is only known when it runs"
            )
            return []
    roots = _roots([*(backend for backend in backends if backend is not None), *([run] if run is not None else [])])
    try:
        catalog: Any = _run_to_end(lambda: capability.load(run))
    except Exception as exc:
        message = str(exc)
        for root in roots:
            message = message.replace(root, ".").replace(root.replace("\\", "/"), ".")
        _compat.add_warning(warnings, f"the skills of `Skills` could not be read ({type(exc).__name__}: {message})")
        return []
    for skipped in catalog.skipped:
        path = _relative(str(skipped.path), roots, None)
        words = _SKIPPED.get(str(skipped.reason), "it could not be read")
        _compat.add_warning(warnings, f"skill `{path}` was left out: {words}")
    return [
        SkillInfo(
            name=clip(skill.name),
            description=clip(skill.description),
            when_to_use=clip(skill.when_to_use),
            model_invocable=skill.model_invocable is not False,
            path=clip(_relative(str(skill.path), roots, f"{skill.name}/SKILL.md")),
            body=clip(skill.body),
        )
        for skill in catalog.skills
    ]


def _innermost(workspace: Any) -> Any:
    """The backend at the bottom of a ``Workspace`` facade and its wrappers, as the harness unwraps it."""
    for _ in range(8):
        held = getattr(workspace, "wrapped", None) or getattr(workspace, "backend", None)
        if held is None or held is workspace:
            break
        workspace = held
    return workspace


def _run_workspace(capabilities: list[Any], local: type[Any], windows: type[Any]) -> Any:
    """A host backend for the run's workspace, when the agent gets it from a ``LocalWorkspace`` capability."""
    for capability in capabilities:
        names = [base.__name__ for base in type(capability).__mro__ if base.__module__.startswith("pydantic_ai.")]
        working_dir: Any = getattr(capability, "working_dir", None)
        if not isinstance(working_dir, str | Path):
            continue
        try:
            if "WindowsWorkspace" in names:
                return windows(working_dir)
            if "LocalWorkspace" in names:
                return local(working_dir)
        except Exception:  # a backend this platform does not have
            return None
    return None


def _roots(backends: list[Any]) -> list[str]:
    """The directories of the backends, as given and with links resolved: what is cut off the paths read."""
    roots: list[str] = []
    for backend in backends:
        ref: Any = getattr(backend, "ref", None)
        root: Any = getattr(ref, "id", None)
        if isinstance(root, str) and root:
            roots.extend(item for item in (root, str(Path(root).resolve())) if item not in roots)
    return roots


def _relative(path: str, roots: list[str], fallback: str | None) -> str:
    """``path`` with forward slashes, relative to the first of ``roots`` it lies in; else ``fallback``.

    Without a ``fallback``, the last two parts of the path, which name the skill and its file.
    """
    posix = path.replace("\\", "/")
    for root in roots:
        prefix = root.replace("\\", "/").rstrip("/") + "/"
        if os.path.normcase(posix).startswith(os.path.normcase(prefix)):
            return posix[len(prefix) :]
    return fallback if fallback is not None else "/".join(posix.rstrip("/").split("/")[-2:])


def _run_to_end(start: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Run the coroutine ``start`` makes; in a thread of its own when this thread already runs an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(start())
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(lambda: asyncio.run(start())).result(timeout=LOAD_TIMEOUT_SECONDS)
    finally:
        pool.shutdown(wait=False)
