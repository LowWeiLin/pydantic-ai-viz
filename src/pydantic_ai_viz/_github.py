"""Fetching the Python source of a GitHub repository, to be read like any directory.

A repository is named by its URL, ``https://github.com/owner/repo``, optionally
continued as GitHub itself does (``/tree/<ref>/<subdir>``, ``/blob/<ref>/<file>``,
``/commit/<sha>``), or by the short form ``gh:owner/repo[@ref][/subdir]``. A ref is
a branch, a tag or a full commit SHA.

``git`` does the fetching: one commit, no history, and of that commit only the
``.py`` files and the few files that mark where a project starts. The result is
kept in a cache directory named after the commit, so a commit is fetched once and
a target pinned to a full SHA is read from the cache without touching the network.
The fetched code is only ever read. No hook, build step or line of it runs.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GITHUB = "https://github.com"
"""Where repositories are fetched from. The tests point this at a directory of bare repositories."""

CACHE_VARIABLE = "PYDANTIC_AI_VIZ_CACHE_DIR"
"""Environment variable that names the cache directory, instead of the user's own cache."""

KEPT_FILES = ("*.py", "pyproject.toml", "setup.cfg", "requirements.txt")
"""What is written to disk of a fetched commit, as sparse-checkout patterns."""

_EXTENDED = "\\\\?\\"
"""The prefix of an extended-length Windows path: two backslashes, a question mark, a backslash."""
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
_SEGMENT = re.compile(r"[A-Za-z0-9_@+~#%=,()\[\] .-]+")
_SHA = re.compile(r"[0-9a-fA-F]{40}")
_URL = re.compile(r"(?i)https?://(?:www\.)?github\.com(?:/(.*))?$")
_OTHER_REMOTE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]*://|git@)")


class FetchError(Exception):
    """A repository reference that is malformed, or a fetch that failed."""


@dataclass(frozen=True, slots=True)
class Reference:
    """A GitHub repository, and what the target says about where to look in it."""

    owner: str
    repo: str
    rest: tuple[str, ...]
    """Path segments after the repository: the ref (when ``has_ref``), then the directory or file."""
    has_ref: bool

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True, slots=True)
class Checkout:
    """The files of one commit, on disk."""

    path: Path
    sha: str
    subpath: tuple[str, ...]
    """The directory or file the target names inside the repository; empty for all of it."""
    ref: str | None
    """The branch or tag the target named, if it named one."""


def looks_remote(spec: str) -> bool:
    """Whether a target is a URL or a ``gh:`` reference, well formed or not."""
    return spec.lower().startswith("gh:") or _OTHER_REMOTE.match(spec) is not None


def parse(spec: str) -> Reference | None:
    """Read a GitHub reference. ``None`` when ``spec`` is not a URL or a ``gh:`` reference at all.

    Raises:
        FetchError: If it is one, but not one that can be fetched.
    """
    if spec.lower().startswith("gh:"):
        return _short_form(spec)
    match = _URL.match(spec)
    if match is None:
        if _OTHER_REMOTE.match(spec):
            raise FetchError(
                f"cannot read {spec!r}: of remote code only GitHub repositories are fetched, as "
                "https://github.com/owner/repo[/tree/<ref>[/subdir]] or gh:owner/repo[@ref][/subdir]"
            )
        return None
    path = (match.group(1) or "").split("#")[0].split("?")[0]
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2:
        raise FetchError(f"cannot read {spec!r}: a GitHub URL names a repository, as https://github.com/owner/repo")
    owner, repo, *rest = segments
    has_ref = False
    if rest:
        kind, *rest = rest
        if kind not in ("tree", "blob", "commit") or not rest:
            raise FetchError(
                f"cannot read {spec!r}: after the repository a GitHub URL continues with "
                "/tree/<ref>[/subdir], /blob/<ref>/<file> or /commit/<sha>"
            )
        has_ref = True
    return _reference(spec, owner, repo.removesuffix(".git"), rest, has_ref)


def _short_form(spec: str) -> Reference:
    body = spec[3:]
    location, at, ref = body.partition("@")
    segments = [segment for segment in location.split("/") if segment]
    if len(segments) < 2 or (at and len(segments) > 2):
        raise FetchError(f"cannot read {spec!r}: the short form is gh:owner/repo[@ref][/subdir]")
    owner, repo, *rest = segments
    if at:
        rest = [segment for segment in ref.split("/") if segment]
        if not rest:
            raise FetchError(f"cannot read {spec!r}: nothing follows the @")
    return _reference(spec, owner, repo, rest, bool(at))


def _reference(spec: str, owner: str, repo: str, rest: list[str], has_ref: bool) -> Reference:
    if not _NAME.fullmatch(owner) or not _NAME.fullmatch(repo):
        raise FetchError(f"cannot read {spec!r}: {owner}/{repo} is not the name of a GitHub repository")
    for segment in rest:
        # Nothing here may read as an option to git or step out of the checkout.
        if not _SEGMENT.fullmatch(segment) or segment.startswith("-") or segment in (".", ".."):
            raise FetchError(f"cannot read {spec!r}: {segment!r} is not part of a ref or a path that can be fetched")
    return Reference(owner, repo, tuple(rest), has_ref)


def cache_dir() -> Path:
    """The directory fetched repositories are kept in."""
    override = os.environ.get(CACHE_VARIABLE)
    if override:
        return Path(override)
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return base / "pydantic-ai-viz" / "Cache"
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "pydantic-ai-viz"
    return Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache") / "pydantic-ai-viz"


def fetch(reference: Reference, *, timeout: float = 300.0) -> Checkout:
    """Make the commit a reference names available on disk, fetching it unless it is cached.

    Raises:
        FetchError: If git is missing, the repository or the ref does not exist,
            or fetching takes longer than ``timeout`` seconds.
    """
    url = f"{GITHUB}/{reference.owner}/{reference.repo}.git"
    git = _Git(reference.slug, time.monotonic() + timeout)
    sha, subpath, ref = _resolve(reference, url, git)
    try:
        home = cache_dir() / "github" / reference.owner.lower() / reference.repo.lower()
        final = home / sha
        if not final.is_dir():
            home.mkdir(parents=True, exist_ok=True)
            _download(url, sha, final, git)
    except OSError as exc:
        raise FetchError(f"cannot keep {reference.slug} in the cache directory ({exc})") from None
    return Checkout(_long(final.resolve()), sha, subpath, ref)


def _resolve(reference: Reference, url: str, git: _Git) -> tuple[str, tuple[str, ...], str | None]:
    """``(commit sha, path inside the repository, ref name)`` for a reference."""
    rest = reference.rest
    if not reference.has_ref:
        listing = git.run("ls-remote", url, "HEAD")
        sha = listing.split()[0] if listing.split() else ""
        if not _SHA.fullmatch(sha):
            raise FetchError(f"{reference.slug} has no default branch to read; name a ref")
        return sha.lower(), rest, None
    if _SHA.fullmatch(rest[0]):
        return rest[0].lower(), rest[1:], None
    refs = _remote_refs(git.run("ls-remote", "--heads", "--tags", url))
    # A branch name may contain slashes, so ``tree/a/b/c`` is read as the longest ref it starts with.
    for length in range(len(rest), 0, -1):
        name = "/".join(rest[:length])
        if name in refs:
            return refs[name], rest[length:], name
    raise FetchError(
        f"{reference.slug} has no branch or tag named {rest[0]!r}. A commit is named by its full 40-character SHA"
    )


def _remote_refs(listing: str) -> dict[str, str]:
    """Branch and tag names -> the commit each points at, from ``git ls-remote`` output."""
    tags: dict[str, str] = {}
    heads: dict[str, str] = {}
    for line in listing.splitlines():
        sha, _, name = line.partition("\t")
        if not _SHA.fullmatch(sha):
            continue
        if name.startswith("refs/heads/"):
            heads[name.removeprefix("refs/heads/")] = sha.lower()
        elif name.startswith("refs/tags/"):
            tag = name.removeprefix("refs/tags/")
            if tag.endswith("^{}"):
                tags[tag.removesuffix("^{}")] = sha.lower()  # the commit an annotated tag points at
            else:
                tags.setdefault(tag, sha.lower())
    return {**tags, **heads}


def _download(url: str, sha: str, final: Path, git: _Git) -> None:
    """Fetch one commit into ``final``. Only whole checkouts ever carry that name."""
    failure: FetchError | None = None
    # First without file contents, so that only the files that are kept get downloaded.
    # A server or a git that cannot do that gets the plain request.
    for partial in (True, False):
        staging = Path(tempfile.mkdtemp(prefix=f".{sha[:12]}-", dir=final.parent))
        try:
            _checkout(url, sha, staging, git, partial=partial)
            _remove(staging / ".git")
            try:
                staging.rename(final)
            except OSError:
                if not final.is_dir():  # not merely another process that got there first
                    raise
            return
        except FetchError as exc:
            failure = failure or exc
            if git.out_of_time:
                break
        finally:
            if staging.exists():
                _remove(staging)
    assert failure is not None
    raise failure


def _checkout(url: str, sha: str, directory: Path, git: _Git, *, partial: bool) -> None:
    git.run("init", "-q", cwd=directory)
    git.run("remote", "add", "origin", url, cwd=directory)
    patterns = directory / ".git" / "info" / "sparse-checkout"
    patterns.parent.mkdir(parents=True, exist_ok=True)
    patterns.write_text("".join(f"{pattern}\n" for pattern in KEPT_FILES), encoding="utf-8")
    filtered = ["--filter=blob:none"] if partial else []
    git.run("fetch", "-q", "--depth", "1", "--no-tags", *filtered, "origin", sha, cwd=directory)
    git.run("checkout", "-q", "--detach", "FETCH_HEAD", cwd=directory)


def _long(path: Path) -> Path:
    """``path`` in the form Windows accepts beyond 260 characters, which a deep repository soon needs."""
    text = str(path)
    if sys.platform != "win32" or text.startswith(_EXTENDED):
        return path
    if text.startswith(r"\\"):
        return Path(_EXTENDED + "UNC" + text[1:])
    return Path(_EXTENDED + text)


def _remove(directory: Path) -> None:
    """Delete a directory tree, read-only files included. What cannot be deleted is left behind."""
    directory = _long(directory.absolute())

    def writable(function: Callable[..., Any], path: str, _error: object) -> None:
        with contextlib.suppress(OSError):
            os.chmod(path, stat.S_IWRITE)
            function(path)

    if sys.version_info >= (3, 12):
        shutil.rmtree(directory, onexc=writable)
    else:
        shutil.rmtree(directory, onerror=writable)


class _Git:
    """Runs git for one fetch, within the time the fetch has left."""

    _SETTINGS = (
        "core.symlinks=false",  # a link in the repository is written as a small file, never as a link
        "core.autocrlf=false",
        "core.longpaths=true",
        "core.sparseCheckout=true",
        "core.sparseCheckoutCone=false",
        "advice.detachedHead=false",
    )

    def __init__(self, slug: str, deadline: float) -> None:
        self.slug = slug
        self.deadline = deadline
        self.out_of_time = False

    def run(self, *arguments: str, cwd: Path | None = None) -> str:
        """Run one git command and return what it printed.

        Raises:
            FetchError: If git is not installed, fails or runs out of time.
        """
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.out_of_time = True
            raise FetchError(f"fetching {self.slug} took too long and was stopped")
        command = ["git"]
        for setting in self._SETTINGS:
            command += ["-c", setting]
        environment = {
            **os.environ,
            # Never stop to ask for a password: a repository that needs one is reported as not readable.
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "LC_ALL": "C",
        }
        try:
            done = subprocess.run(
                [*command, *arguments],
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=remaining,
                check=False,
            )
        except FileNotFoundError:
            raise FetchError("git is needed to read a GitHub repository, and no git program was found") from None
        except subprocess.TimeoutExpired:
            self.out_of_time = True
            raise FetchError(f"fetching {self.slug} took too long and was stopped") from None
        if done.returncode != 0:
            lines = [line.strip() for line in done.stderr.splitlines() if line.strip()]
            said = next((line for line in reversed(lines) if line.startswith(("fatal:", "error:"))), "")
            said = said or (lines[-1] if lines else f"git {arguments[0]} failed")
            hint = ""
            if any(word in done.stderr for word in ("not found", "Username", "Authentication", "does not appear")):
                hint = " (is the repository public, and is its name spelled right?)"
            raise FetchError(f"git could not fetch {self.slug}: {said}{hint}")
        return done.stdout
