"""Turning a target given by the user into source files to analyse.

A target is one of:

* the path of a ``.py`` file, optionally followed by ``:name`` to pick one agent
  or graph in it;
* the path of a directory, whose ``.py`` files are all read, however deep;
* a dotted module name such as ``app.agents``, optionally followed by ``:name``.
  It is found by looking for ``app/agents.py`` (or the package ``app/agents/``)
  below the working directory, its ``src`` directory and the entries of
  ``sys.path``;
* a GitHub repository, as ``https://github.com/owner/repo[/tree/<ref>[/subdir]]``
  or ``gh:owner/repo[@ref][/subdir]``, which ``_github`` fetches with git.

Locating a target only looks at the file system (and, for GitHub, runs git). It
never imports anything.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .. import _github

MARKERS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "uv.lock",
    "poetry.lock",
    "Pipfile",
    ".git",
    ".hg",
)
"""A directory that holds one of these is where a project starts."""

SKIPPED_DIRECTORIES = frozenset({"venv", "node_modules", "__pycache__", "site-packages", "build", "dist"})
"""Directories that hold installed or generated code, not the project's own. Hidden ones are skipped too."""

MAX_DIRECTORY_DEPTH = 40
"""How far below a directory target files are looked for; deeper than this is a loop, not a project."""

_MAX_PROJECT_LEVELS = 8
"""How far above a target the start of its project is looked for."""


class TargetError(ValueError):
    """A target that does not name something that can be shown."""


@dataclass(frozen=True, slots=True)
class Source:
    """One file to analyse, and the name selected in it, if any."""

    path: Path
    select: str | None
    spec: str
    """The target as the user wrote it, for messages."""


@dataclass(frozen=True, slots=True)
class Place:
    """Where a target is on disk, before the files below it are listed."""

    spec: str
    path: Path
    """The file or the directory the target names."""
    select: str | None
    root: Path
    """The directory that the names of its files are given relative to."""
    boundary: Path | None
    """The directory inside which a file may be read because another one imports it; ``None`` for nowhere."""
    title: str
    notices: tuple[str, ...] = ()


def split_target(spec: str) -> tuple[str, str | None]:
    """``(location, name)``. A Windows drive colon is not a name separator."""
    if spec.lower().endswith(".py"):
        return spec, None
    location, colon, name = spec.rpartition(":")
    if colon and location.lower().endswith(".py"):
        return location, name
    return spec, None


def is_remote(target: str | os.PathLike[str]) -> bool:
    """Whether a target names code that would have to be fetched, as opposed to code on this machine."""
    return _github.looks_remote(os.fspath(target))


def skipped_directory(name: str) -> bool:
    """Whether a directory of this name is passed over when files are looked for."""
    return name in SKIPPED_DIRECTORIES or name.startswith(".") or name.endswith(".egg-info")


def project_root(start: Path, *, within: Path | None = None) -> Path | None:
    """The directory where the project around ``start`` begins; ``None`` when there is none.

    That is the nearest directory, from ``start`` upwards, that holds a project
    file such as ``pyproject.toml`` or a ``.git``. Without such a directory it is the
    parent of the outermost package ``start`` is in, and ``start`` itself otherwise.
    The home directory, the directory for temporary files and the root of a drive
    are never taken for one: many projects live below them. Where the answer would
    be one of those, it is ``start``, or ``None`` when ``start`` is one itself.

    With ``within``, the search ends at that directory, which is the answer when
    nothing nearer is found: a fetched repository is one project at most.
    """
    shared: set[Path] = set()  # directories that many projects live below, and that are none themselves
    for common in (Path.home, lambda: Path(tempfile.gettempdir())):
        with contextlib.suppress(OSError, RuntimeError):
            shared.add(common().resolve())
    current = start
    for _ in range(_MAX_PROJECT_LEVELS if within is None else 64):
        if current == within:
            return current
        if current in shared or current.parent == current:
            break
        if any((current / marker).exists() for marker in MARKERS):
            return current
        current = current.parent
    if within is not None:
        return within
    current = start
    for _ in range(64):
        if not (current / "__init__.py").is_file() or current.parent == current:
            break
        current = current.parent

    def shared_or_drive(directory: Path) -> bool:
        return directory in shared or directory.parent == directory

    if shared_or_drive(current):
        return None if shared_or_drive(start) else start
    return current


def place(target: str | os.PathLike[str], *, fetch_seconds: float = 300.0) -> Place:
    """Find what a target names. A GitHub target is fetched here; nothing else touches the network.

    Raises:
        TargetError: If the target names nothing that can be read.
    """
    spec = os.fspath(target)
    location, select = split_target(spec)
    if select is not None and not select.isidentifier():
        raise TargetError(f"not a name: {select!r} in {spec}")
    try:
        reference = _github.parse(location)
    except _github.FetchError as exc:
        raise TargetError(str(exc)) from None
    if reference is not None:
        return _remote(spec, reference, select, fetch_seconds)
    path = Path(location)
    if path.is_dir():
        return _directory(spec, path.resolve())
    if location.lower().endswith(".py"):
        if not path.is_file():
            raise TargetError(f"file not found: {location}")
        return _file(spec, path.resolve(), select)
    if path.is_file():
        raise TargetError(f"{location} is not a .py file; only Python source can be read")
    return _module(spec)


def _file(spec: str, path: Path, select: str | None) -> Place:
    return Place(spec, path, select, root=path.parent, boundary=project_root(path.parent), title=path.stem)


def _directory(spec: str, path: Path) -> Place:
    return Place(spec, path, None, root=path, boundary=project_root(path), title=path.name or spec)


def _remote(spec: str, reference: _github.Reference, select: str | None, fetch_seconds: float) -> Place:
    try:
        checkout = _github.fetch(reference, timeout=fetch_seconds)
    except _github.FetchError as exc:
        raise TargetError(f"{spec}: {exc}") from None
    path = checkout.path.joinpath(*checkout.subpath)
    inside = "/".join(checkout.subpath)
    if not path.exists():
        raise TargetError(f"{spec}: {reference.slug} has no {inside!r} at commit {checkout.sha[:12]}")
    if path.is_file() and not path.name.lower().endswith(".py"):
        raise TargetError(f"{spec}: {inside} is not a .py file; only Python source can be read")
    return Place(
        spec,
        path,
        select,
        # Named from the top of the repository, so a file reads as it does in a GitHub link.
        root=checkout.path,
        boundary=project_root(path if path.is_dir() else path.parent, within=checkout.path),
        title=f"{reference.slug}@{checkout.sha[:7]}",
        notices=(f"read {reference.slug} from GitHub at commit {checkout.sha}; none of its code was run",),
    )


def _module(spec: str) -> Place:
    """A dotted module name, found on disk the way an import would find it, without importing."""
    if "/" in spec or "\\" in spec:
        raise TargetError(f"no file or directory at {spec}")
    dotted, colon, select = spec.partition(":")
    parts = dotted.split(".")
    if not dotted or not all(part.isidentifier() for part in parts):
        raise TargetError(
            f"cannot read {spec!r}: it is not the path of a .py file or of a directory, a module name, "
            "or a GitHub repository (https://github.com/owner/repo or gh:owner/repo)"
        )
    if colon and not select.isidentifier():
        raise TargetError(f"not a name: {select!r} in {spec}")
    working = Path.cwd().resolve()
    for base, installed in _search_path(working):
        found = _module_below(base, parts, namespace=not installed)
        if found is None:
            continue
        package = found.is_dir()
        boundary: Path | None = project_root(base)
        if installed:
            # Installed code: nothing outside the package itself is read on its behalf.
            boundary = base / parts[0] if (base / parts[0]).is_dir() else None
        if package and colon:
            initializer = found / "__init__.py"
            if not initializer.is_file():
                raise TargetError(f"{spec}: {dotted} is a directory without an __init__.py, so it defines no names")
            found = initializer
        return Place(spec, found, select if colon else None, root=base, boundary=boundary, title=dotted)
    looked = " or ".join(dict.fromkeys(["/".join(parts) + ".py", "/".join(parts) + "/__init__.py"]))
    raise TargetError(
        f"no module named {dotted!r}: looked for {looked} below {working}, its src directory and the "
        "entries of sys.path. Nothing is imported to find a module; pass the path of the file or directory, "
        "or use --import to import it by name (this runs its code)"
    )


def _search_path(working: Path) -> list[tuple[Path, bool]]:
    """Directories a module name is looked for in, each with whether it holds installed code."""
    found: dict[Path, bool] = {working: False, working / "src": False}
    prefixes = {Path(prefix).resolve() for prefix in (sys.prefix, sys.base_prefix)}
    for entry in sys.path:
        if not entry:
            continue
        try:
            directory = Path(entry).resolve()
            if not directory.is_dir():
                continue
        except OSError:
            continue
        installed = directory.name in ("site-packages", "dist-packages") or any(
            prefix == directory or prefix in directory.parents for prefix in prefixes
        )
        found.setdefault(directory, installed)
    return list(found.items())


def _module_below(base: Path, parts: list[str], *, namespace: bool) -> Path | None:
    """The file ``base/a/b.py`` or the package directory ``base/a/b`` for the module ``a.b``."""
    target = base.joinpath(*parts)
    module = target.with_name(target.name + ".py")
    try:
        if module.is_file():
            return module
        if (target / "__init__.py").is_file() or (namespace and target.is_dir()):
            return target
    except OSError:
        return None
    return None


def sources_of(place: Place, *, max_files: int, deadline: float) -> tuple[list[Source], list[str]]:
    """The files a place stands for, and notices about what was left out."""
    if not place.path.is_dir():
        return [Source(place.path, place.select, place.spec)], []
    # What the directory is called in notices: a checkout is named after its repository, not its cache folder.
    shown = place.title if place.path == place.root else place.path.relative_to(place.root).as_posix()
    files, notices = python_files(place.path, max_files=max_files, deadline=deadline, name=shown)
    if not files and not notices:
        notices.append(f"{place.spec} holds no .py files")
    return [Source(path, None, place.spec) for path in files], notices


def python_files(
    directory: Path, *, max_files: int, deadline: float, name: str | None = None
) -> tuple[list[Path], list[str]]:
    """Every ``.py`` file below ``directory``: a directory's own files first, then its subdirectories, by name.

    Directories of installed or generated code are passed over: virtual environments,
    ``node_modules``, ``site-packages``, ``__pycache__``, ``build``, ``dist`` and every
    hidden directory. Links, to files or to directories, are not followed: either can
    lead out of the directory, or back into it. The search ends, with a
    notice, at ``max_files`` files or at ``deadline`` (a ``time.monotonic()`` value).
    ``name`` is what the directory is called in notices.
    """
    name = name or directory.name
    found: list[Path] = []
    notices: list[str] = []
    pending: list[tuple[Path, int]] = [(directory, 0)]
    while pending:
        current, depth = pending.pop()
        if time.monotonic() >= deadline:
            notices.append(
                f"stopped looking for files below {name} at the time limit; "
                f"{current.relative_to(directory).as_posix()} and what follows were not read"
            )
            break
        try:
            with os.scandir(current) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
        except OSError as exc:
            where = current.relative_to(directory).as_posix()
            notices.append(f"skipped the directory {where}: it cannot be read ({exc.strerror or type(exc).__name__})")
            continue
        below: list[tuple[Path, int]] = []
        for entry in entries:
            try:
                if is_link(entry):
                    continue
                is_directory = entry.is_dir(follow_symlinks=False)
                is_file = not is_directory and entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_directory:
                if skipped_directory(entry.name) or os.path.exists(os.path.join(entry.path, "pyvenv.cfg")):
                    continue
                if depth + 1 > MAX_DIRECTORY_DEPTH:
                    where = Path(entry.path).relative_to(directory).as_posix()
                    notices.append(f"skipped the directory {where}: it is nested too deeply")
                    continue
                below.append((Path(entry.path), depth + 1))
            elif is_file and entry.name.lower().endswith(".py"):
                if len(found) >= max_files:
                    notices.append(
                        f"{name} holds more than {max_files:,} .py files; only the first {max_files:,} were read"
                    )
                    return found, notices
                found.append(Path(entry.path))
        pending.extend(reversed(below))
    return found, notices


def is_link(entry: os.DirEntry[str]) -> bool:
    """A symbolic link or, on Windows, a junction: either can lead out of the directory or back into it."""
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)
    return bool(is_junction()) if is_junction is not None else False
