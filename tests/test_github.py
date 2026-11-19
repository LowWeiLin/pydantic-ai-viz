"""GitHub targets: reading a reference, fetching a commit with git, and analysing what was fetched.

No test here touches the network. "GitHub" is a directory of bare repositories on
this machine, which git fetches from exactly as it would from the real one.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from pydantic_ai_viz import Limits, _github, analyze, cli
from pydantic_ai_viz._analyze import TargetError, is_remote
from pydantic_ai_viz._github import Checkout, FetchError, Reference, fetch, parse
from pydantic_ai_viz._models import SourceLocation

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

AGENTS = '''
from pathlib import Path

from pydantic_ai import Agent

from .models import Reply

Path("ran.txt").write_text("the fetched code was executed")

helper = Agent("openai:gpt-5.2", name="helper", output_type=Reply)
lead = Agent("openai:gpt-5.2", name="lead", instructions="Lead.")


@lead.tool_plain
async def ask_helper(question: str) -> str:
    """Ask the helper."""
    return (await helper.run(question)).output.text
'''


@dataclass
class Hub:
    """A stand-in for github.com: ``<base>/<owner>/<repo>.git`` are bare repositories."""

    base: Path
    work: Path
    first: str = ""
    """The first commit of ``acme/shop``: the tag ``v1``."""
    second: str = ""
    """The second commit: the tip of ``main``."""
    feature: str = ""
    """The tip of the branch ``feature/new-agents``."""

    def git(self, *arguments: str, cwd: Path | None = None) -> str:
        done = subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "-c",
                "commit.gpgsign=false",
                *arguments,
            ],
            cwd=cwd or self.work,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    def write(self, name: str, content: str) -> None:
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8", newline="\n")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


@pytest.fixture(scope="module")
def hub(tmp_path_factory: pytest.TempPathFactory) -> Hub:
    root = tmp_path_factory.mktemp("hub")
    hub = Hub(base=root / "remote", work=root / "work")
    hub.work.mkdir()
    hub.git("init", "-q", "-b", "main")
    hub.write("README.md", "# shop\n")
    hub.write("pyproject.toml", "[project]\nname = 'shop'\n")
    hub.write("data/big.bin", "not source\n")
    hub.write("src/shop/__init__.py", "")
    hub.write("src/shop/models.py", "from pydantic import BaseModel\n\n\nclass Reply(BaseModel):\n    text: str\n")
    hub.write("src/shop/agents.py", AGENTS)
    hub.first = hub.commit("first")
    hub.git("tag", "-a", "v1", "-m", "version 1")  # an annotated tag: it is an object of its own

    hub.write("src/shop/extra.py", "from pydantic_ai import Agent\n\nextra = Agent('test', name='extra')\n")
    hub.write("examples/demo.py", "from pydantic_ai import Agent\n\ndemo = Agent('test', name='demo')\n")
    hub.write("examples/requirements.txt", "pydantic-ai\n")
    # A symbolic link in the repository, added without needing the right to create one here.
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=hub.work,
        input="../../../outside.py",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    hub.git("add", "-A")
    hub.git("update-index", "--add", "--cacheinfo", f"120000,{blob},src/shop/link.py")
    hub.git("commit", "-q", "-m", "second")
    hub.second = hub.git("rev-parse", "HEAD")

    hub.git("checkout", "-q", "-b", "feature/new-agents")
    hub.write("src/shop/feature.py", "from pydantic_ai import Agent\n\nfeature = Agent('test', name='feature')\n")
    hub.git("add", "src/shop/feature.py")
    hub.git("commit", "-q", "-m", "feature")
    hub.feature = hub.git("rev-parse", "HEAD")
    hub.git("checkout", "-q", "main")

    bare = hub.base / "acme" / "shop.git"
    bare.parent.mkdir(parents=True)
    hub.git("clone", "-q", "--bare", str(hub.work), str(bare))
    # What GitHub allows: asking for any commit by its SHA, and for a commit without its file contents.
    hub.git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=bare)
    hub.git("config", "uploadpack.allowFilter", "true", cwd=bare)
    # A second repository that allows neither, as an older or stricter server would.
    plain = hub.base / "acme" / "plain.git"
    hub.git("clone", "-q", "--bare", str(hub.work), str(plain))
    return hub


@pytest.fixture
def cache(hub: Hub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the fetcher at the stand-in hub and at an empty cache."""
    monkeypatch.setattr(_github, "GITHUB", hub.base.as_uri())
    monkeypatch.setenv(_github.CACHE_VARIABLE, str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)
    yield tmp_path / "cache"
    assert not (tmp_path / "ran.txt").exists(), "fetched code was executed"


def names_in(checkout: Checkout) -> list[str]:
    return sorted(path.relative_to(checkout.path).as_posix() for path in checkout.path.rglob("*") if path.is_file())


# -------------------------------------------------------------- references


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("https://github.com/acme/shop", Reference("acme", "shop", (), False)),
        ("https://github.com/acme/shop/", Reference("acme", "shop", (), False)),
        ("https://github.com/acme/shop.git", Reference("acme", "shop", (), False)),
        ("HTTPS://WWW.GitHub.com/Acme/Shop", Reference("Acme", "Shop", (), False)),
        ("http://github.com/acme/shop", Reference("acme", "shop", (), False)),
        ("https://github.com/acme/shop/tree/main", Reference("acme", "shop", ("main",), True)),
        ("https://github.com/acme/shop/tree/main/src/shop", Reference("acme", "shop", ("main", "src", "shop"), True)),
        (
            "https://github.com/acme/shop/blob/v1/src/shop/agents.py#L10-L20",
            Reference("acme", "shop", ("v1", "src", "shop", "agents.py"), True),
        ),
        ("https://github.com/acme/shop/tree/main?tab=readme", Reference("acme", "shop", ("main",), True)),
        ("https://github.com/acme/shop/commit/" + "a" * 40, Reference("acme", "shop", ("a" * 40,), True)),
        ("gh:acme/shop", Reference("acme", "shop", (), False)),
        ("GH:acme/shop", Reference("acme", "shop", (), False)),
        ("gh:acme/shop/src/shop", Reference("acme", "shop", ("src", "shop"), False)),
        ("gh:acme/shop@main", Reference("acme", "shop", ("main",), True)),
        ("gh:acme/shop@feature/new-agents/src", Reference("acme", "shop", ("feature", "new-agents", "src"), True)),
        ("gh:acme/my.repo-name_2@v1.0.0", Reference("acme", "my.repo-name_2", ("v1.0.0",), True)),
    ],
)
def test_a_github_reference_is_read_from_a_url_or_the_short_form(spec: str, expected: Reference) -> None:
    assert parse(spec) == expected
    assert _github.looks_remote(spec)
    assert is_remote(spec)
    assert expected.slug == f"{expected.owner}/{expected.repo}"


@pytest.mark.parametrize("spec", ["agents.py", "src/app", "pkg.module:name", r"C:\work\agents.py", "github.com", "gh"])
def test_what_is_not_a_url_or_a_short_form_is_not_a_github_reference(spec: str) -> None:
    assert parse(spec) is None
    assert not is_remote(spec)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("https://gitlab.com/acme/shop", "of remote code only GitHub repositories are fetched"),
        ("ssh://git@github.com/acme/shop.git", "of remote code only GitHub repositories are fetched"),
        ("git@github.com:acme/shop.git", "of remote code only GitHub repositories are fetched"),
        ("file:///tmp/repo", "of remote code only GitHub repositories are fetched"),
        ("https://github.com/", "a GitHub URL names a repository"),
        ("https://github.com/acme", "a GitHub URL names a repository"),
        ("https://github.com/acme/shop/pull/7", "/tree/<ref>[/subdir], /blob/<ref>/<file> or /commit/<sha>"),
        ("https://github.com/acme/shop/tree", "/tree/<ref>[/subdir], /blob/<ref>/<file> or /commit/<sha>"),
        ("gh:acme", "the short form is gh:owner/repo[@ref][/subdir]"),
        ("gh:acme/shop/sub@main", "the short form is gh:owner/repo[@ref][/subdir]"),
        ("gh:acme/shop@", "nothing follows the @"),
        ("gh:ac me/shop", "ac me/shop is not the name of a GitHub repository"),
        ("gh:acme/shop@../../etc", "'..' is not part of a ref or a path that can be fetched"),
        ("gh:acme/shop@--upload-pack=touch", "is not part of a ref or a path that can be fetched"),
        ("gh:acme/shop@main/a;b", "'a;b' is not part of a ref or a path that can be fetched"),
        ("gh:--evil/shop", "--evil/shop is not the name of a GitHub repository"),
    ],
)
def test_a_reference_that_cannot_be_fetched_says_why(spec: str, message: str) -> None:
    with pytest.raises(FetchError) as exc_info:
        parse(spec)
    assert message in str(exc_info.value)
    assert is_remote(spec)


def test_branch_and_tag_names_are_read_from_what_git_lists() -> None:
    listing = (
        f"{'1' * 40}\trefs/heads/main\n"
        f"{'2' * 40}\trefs/heads/feature/x\n"
        f"{'3' * 40}\trefs/tags/v1\n"
        f"{'4' * 40}\trefs/tags/v1^{{}}\n"
        f"{'5' * 40}\trefs/tags/light\n"
        f"{'6' * 40}\trefs/tags/main\n"
        "garbage line\n"
        f"{'7' * 40}\trefs/pull/1/head\n"
    )
    assert _github._remote_refs(listing) == {  # pyright: ignore[reportPrivateUsage]
        "main": "1" * 40,  # a branch wins over a tag of the same name
        "feature/x": "2" * 40,
        "v1": "4" * 40,  # the commit an annotated tag points at, not the tag object
        "light": "5" * 40,
    }


def test_the_cache_directory_is_the_users_own_unless_one_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_github.CACHE_VARIABLE, "somewhere")
    assert _github.cache_dir() == Path("somewhere")
    monkeypatch.delenv(_github.CACHE_VARIABLE)
    default = _github.cache_dir()
    assert default.is_absolute()
    assert "pydantic-ai-viz" in default.parts
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", "/xdg")
    assert _github.cache_dir() == Path("/xdg") / "pydantic-ai-viz"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert _github.cache_dir() == Path.home() / ".cache" / "pydantic-ai-viz"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _github.cache_dir() == Path.home() / "Library" / "Caches" / "pydantic-ai-viz"


def test_the_only_program_ever_started_is_git() -> None:
    """Fetching a repository is kept out of the analysis package, and all it starts is git, without a shell."""
    path = Path(_github.__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    starts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in ("subprocess", "os")
        and node.func.attr in ("run", "Popen", "call", "check_call", "check_output", "system", "popen", "startfile")
    ]
    assert [ast.unparse(node.func) for node in starts] == ["subprocess.run"]
    assert ast.unparse(starts[0].args[0]) == "[*command, *arguments]"
    assigned = [
        ast.unparse(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "command"
    ]
    assert assigned == ["['git']"]
    assert "shell=" not in source
    banned = {"eval", "exec", "compile", "__import__", "literal_eval", "run_path", "run_module", "import_module"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
            assert name not in banned - ({"compile"} if isinstance(node.func, ast.Attribute) else set()), node.lineno


# ----------------------------------------------------------------- fetching


def test_a_commit_is_fetched_as_its_python_files_and_nothing_else(hub: Hub, cache: Path) -> None:
    checkout = fetch(Reference("acme", "shop", (), False))
    assert (checkout.sha, checkout.subpath, checkout.ref) == (hub.second, (), None)
    assert checkout.path.name == hub.second
    assert names_in(checkout) == [
        "examples/demo.py",
        "examples/requirements.txt",
        "pyproject.toml",
        "src/shop/__init__.py",
        "src/shop/agents.py",
        "src/shop/extra.py",
        "src/shop/link.py",
        "src/shop/models.py",
    ]
    # No README, no data file, and no repository: only what is read, and what marks a project.
    assert not (checkout.path / ".git").exists()
    # The link in the repository arrived as a small file holding its target. It leads nowhere.
    link = checkout.path / "src" / "shop" / "link.py"
    assert not link.is_symlink()
    assert link.read_text(encoding="utf-8") == "../../../outside.py"
    # Nothing is left over from the fetch itself.
    assert [entry.name for entry in checkout.path.parent.iterdir()] == [hub.second]


def test_a_ref_is_a_branch_a_tag_or_a_full_sha_and_the_rest_is_the_path(hub: Hub, cache: Path) -> None:
    def resolved(*rest: str) -> tuple[str, tuple[str, ...], str | None]:
        checkout = fetch(Reference("acme", "shop", rest, True))
        return checkout.sha, checkout.subpath, checkout.ref

    assert resolved("main") == (hub.second, (), "main")
    assert resolved("main", "src", "shop") == (hub.second, ("src", "shop"), "main")
    assert resolved("v1", "src") == (hub.first, ("src",), "v1")
    # A branch with a slash in its name: the longest name that exists is the ref.
    assert resolved("feature", "new-agents", "src", "shop") == (hub.feature, ("src", "shop"), "feature/new-agents")
    # A commit that no branch points at, by its SHA, in either case.
    assert resolved(hub.first, "src") == (hub.first, ("src",), None)
    assert resolved(hub.first.upper()) == (hub.first, (), None)
    first = fetch(Reference("acme", "shop", (hub.first,), True))
    assert "src/shop/extra.py" not in names_in(first)
    assert "src/shop/feature.py" in names_in(fetch(Reference("acme", "shop", (hub.feature,), True)))


def test_a_commit_already_in_the_cache_is_read_without_the_network(
    hub: Hub, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = fetch(Reference("acme", "shop", (hub.second,), True))
    monkeypatch.setattr(_github, "GITHUB", (hub.base / "gone").as_uri())

    def no_git(*args: object, **kwargs: object) -> None:
        raise AssertionError("git was run for a commit that is in the cache")

    monkeypatch.setattr(subprocess, "run", no_git)
    again = fetch(Reference("acme", "shop", (hub.second, "src"), True))
    assert (again.path, again.sha, again.subpath) == (first.path, hub.second, ("src",))
    # A branch has to be looked up, cache or not: what it points at may have changed.
    with pytest.raises(AssertionError, match="git was run"):
        fetch(Reference("acme", "shop", ("main",), True))


def test_a_server_that_only_hands_out_whole_commits_is_asked_the_plain_way(hub: Hub, cache: Path) -> None:
    checkout = fetch(Reference("acme", "plain", ("main",), True))
    assert checkout.sha == hub.second
    assert "src/shop/agents.py" in names_in(checkout)
    assert "README.md" not in names_in(checkout)
    assert [entry.name for entry in checkout.path.parent.iterdir()] == [hub.second]


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        (Reference("acme", "missing", (), False), "git could not fetch acme/missing: "),
        (Reference("acme", "missing", ("main",), True), "git could not fetch acme/missing: "),
        (Reference("acme", "missing", ("a" * 40,), True), "git could not fetch acme/missing: "),
        (Reference("acme", "shop", ("nope",), True), "acme/shop has no branch or tag named 'nope'. A commit is named"),
        (Reference("acme", "shop", ("abc1234",), True), "A commit is named by its full 40-character SHA"),
        (Reference("acme", "shop", ("f" * 40,), True), "git could not fetch acme/shop: "),
    ],
)
def test_a_repository_or_ref_that_does_not_exist_is_an_error_that_names_it(
    hub: Hub, cache: Path, reference: Reference, message: str
) -> None:
    with pytest.raises(FetchError) as exc_info:
        fetch(reference)
    assert message in str(exc_info.value)
    # A failed fetch leaves nothing behind that a later one could take for a commit.
    left = [path for path in cache.rglob("*") if path.is_file()]
    assert left == []


def test_fetching_gives_up_at_its_time_limit_and_without_git_says_git_is_needed(
    hub: Hub, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(FetchError, match="fetching acme/shop took too long and was stopped"):
        fetch(Reference("acme", "shop", ("main",), True), timeout=0)

    def slow(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", slow)
    with pytest.raises(FetchError, match="fetching acme/shop took too long and was stopped"):
        fetch(Reference("acme", "shop", (hub.first,), True), timeout=30)

    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing)
    with pytest.raises(FetchError, match="git is needed to read a GitHub repository, and no git program was found"):
        fetch(Reference("acme", "shop", (), False))


def test_git_is_never_left_waiting_for_a_password(hub: Hub, cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    real = subprocess.run

    def spy(command: list[str], **kwargs: Any) -> Any:
        seen.append({"command": command, **kwargs})
        return real(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    fetch(Reference("acme", "shop", ("main",), True))
    assert len(seen) >= 5
    for call in seen:
        assert call["command"][0] == "git"
        assert call["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert call["env"]["GCM_INTERACTIVE"] == "never"
        assert call["stdin"] == subprocess.DEVNULL
        assert call["timeout"] > 0
        assert "core.symlinks=false" in call["command"]
        assert "shell" not in call


# ---------------------------------------------------------------- analysing


def test_a_repository_is_fetched_and_read_like_a_directory(hub: Hub, cache: Path) -> None:
    found = analyze("gh:acme/shop")
    assert found.title == f"acme/shop@{hub.second[:7]}"
    assert found.notices == [
        f"read acme/shop from GitHub at commit {hub.second}; none of its code was run",
        # The symbolic link, which arrived as a one-line text file.
        "skipped src/shop/link.py: it is not valid Python (invalid syntax on line 1)",
    ]
    # Named from the top of the repository, as the path reads in a GitHub link.
    assert found.files == [
        "examples/demo.py",
        "src/shop/__init__.py",
        "src/shop/agents.py",
        "src/shop/extra.py",
        "src/shop/models.py",
    ]
    assert [(b.name, b.location) for b in found.agents] == [
        ("demo", SourceLocation(file="examples/demo.py", line=3)),
        ("helper", SourceLocation(file="src/shop/agents.py", line=10)),
        ("lead", SourceLocation(file="src/shop/agents.py", line=11)),
        ("extra", SourceLocation(file="src/shop/extra.py", line=3)),
    ]
    lead = found.agents[2]
    assert [(d.tool_name, d.agent_id) for d in lead.delegates] == [("ask_helper", "a2")]
    assert found.agents[1].output.location == SourceLocation(file="src/shop/models.py", line=4)


def test_a_ref_a_subdirectory_a_file_and_a_name_narrow_what_is_read(hub: Hub, cache: Path) -> None:
    base = "https://github.com/acme/shop"
    pinned = analyze(f"{base}/tree/{hub.first}/src")
    assert pinned.title == f"acme/shop@{hub.first[:7]}"
    assert pinned.files == ["src/shop/__init__.py", "src/shop/agents.py", "src/shop/models.py"]
    assert [b.name for b in pinned.agents] == ["helper", "lead"]

    assert [b.name for b in analyze(f"{base}/tree/feature/new-agents/src/shop").agents] == [
        "helper",
        "lead",
        "extra",
        "feature",
    ]
    assert [b.name for b in analyze("gh:acme/shop@v1").agents] == ["helper", "lead"]
    assert [b.name for b in analyze("gh:acme/shop/examples").agents] == ["demo"]

    # One file, as GitHub links to it. The file it imports is read from the same commit.
    one = analyze(f"{base}/blob/main/src/shop/agents.py")
    assert one.files == ["src/shop/agents.py", "src/shop/models.py"]
    assert [b.name for b in one.agents] == ["helper", "lead"]
    assert one.agents[0].output.json_schema is not None
    # And one agent of that file, with the agent it delegates to.
    named = analyze("gh:acme/shop@main/src/shop/agents.py:lead")
    assert [(b.id, b.name) for b in named.agents] == [("a1", "lead"), ("a2", "helper")]


def test_imports_are_followed_inside_the_fetched_repository_only(hub: Hub, cache: Path, tmp_path: Path) -> None:
    # The examples directory has its own requirements file, so it is a project of its own.
    examples = analyze("gh:acme/shop@main/examples")
    assert examples.files == ["examples/demo.py"]
    # The link that points out of the repository is a text file here, and no file outside is ever opened.
    (cache.parent / "outside.py").write_text("from pydantic_ai import Agent\nx = Agent('test')\n", encoding="utf-8")
    found = analyze("gh:acme/shop@main/src")
    assert "src/shop/link.py" not in found.files
    assert [notice.partition(":")[0] for notice in found.notices[1:]] == ["skipped src/shop/link.py"]
    assert all(not name.startswith("..") for name in found.files)


def test_a_repository_and_a_local_target_together_are_named_without_absolute_paths(
    hub: Hub, cache: Path, tmp_path: Path
) -> None:
    local = tmp_path / "work" / "medium" / "good.py"
    local.parent.mkdir(parents=True)
    local.write_text("from pydantic_ai import Agent\nlocal = Agent('test', name='local')\n", encoding="utf-8")
    found = analyze("gh:acme/shop@main/src", local)
    repository = f"acme/shop@{hub.second[:7]}"
    assert found.files == [
        f"{repository}/src/shop/__init__.py",
        f"{repository}/src/shop/agents.py",
        f"{repository}/src/shop/extra.py",
        f"{repository}/src/shop/models.py",
        "medium/good.py",
    ]
    assert [(b.name, b.location) for b in found.agents] == [
        ("helper", SourceLocation(file=f"{repository}/src/shop/agents.py", line=10)),
        ("lead", SourceLocation(file=f"{repository}/src/shop/agents.py", line=11)),
        ("extra", SourceLocation(file=f"{repository}/src/shop/extra.py", line=3)),
        ("local", SourceLocation(file="medium/good.py", line=2)),
    ]
    assert found.agents[0].output.location == SourceLocation(file=f"{repository}/src/shop/models.py", line=4)
    shown = "\n".join([*found.files, *found.notices, *(b.model_dump_json() for b in found.blueprints)])
    for text in (tmp_path.as_posix(), cache.as_posix(), Path.home().name):
        assert text not in shown.replace("\\", "/")


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("gh:acme/shop@main/nowhere", "acme/shop has no 'nowhere' at commit "),
        ("gh:acme/shop@main/pyproject.toml", "pyproject.toml is not a .py file; only Python source can be read"),
        ("gh:acme/shop@nope", "gh:acme/shop@nope: acme/shop has no branch or tag named 'nope'"),
        ("gh:acme/missing", "gh:acme/missing: git could not fetch acme/missing: "),
        ("gh:acme/shop@main/src/shop/agents.py:nope", "no agent or graph is assigned to 'nope' at the top level of"),
        ("gh:acme/shop@main/src/shop/agents.py:1x", "not a name: '1x'"),
    ],
)
def test_a_github_target_that_names_nothing_is_an_error_that_says_why(
    hub: Hub, cache: Path, target: str, message: str
) -> None:
    with pytest.raises(TargetError) as exc_info:
        analyze(target)
    assert message in str(exc_info.value)


def test_the_time_allowed_for_fetching_is_a_limit_of_its_own(hub: Hub, cache: Path) -> None:
    with pytest.raises(TargetError, match="fetching acme/shop took too long"):
        analyze("gh:acme/shop", limits=Limits(max_fetch_seconds=0))
    # Fetching does not use up the time for reading.
    assert len(analyze("gh:acme/shop", limits=Limits(max_fetch_seconds=60)).agents) == 4


# ------------------------------------------------------------- command line


def test_view_fetches_a_repository_reads_it_and_runs_none_of_it(
    hub: Hub, cache: Path, capsys: pytest.CaptureFixture[str], placeholder_viewer: None
) -> None:
    code = cli.main(["view", f"gh:acme/shop@{hub.second}/src", "--export", "shop.html"])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "Wrote HTML export shop.html\n"
    assert captured.err.splitlines() == [
        f"pydantic-ai-viz: note: read acme/shop from GitHub at commit {hub.second}; none of its code was run",
        "pydantic-ai-viz: note: skipped src/shop/link.py: it is not valid Python (invalid syntax on line 1)",
        "Read 4 files without running any code: 3 agents, 0 graphs",
    ]
    page = Path("shop.html").read_text(encoding="utf-8")
    assert f"acme/shop@{hub.second[:7]}" in page
    assert "shop" not in {name.partition(".")[0] for name in sys.modules}


def test_view_never_imports_what_it_fetched(
    hub: Hub, cache: Path, capsys: pytest.CaptureFixture[str], placeholder_viewer: None
) -> None:
    code = cli.main(["view", "--import", "gh:acme/shop", "--export", "shop.html"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.err.startswith("pydantic-ai-viz: error: --import is refused for gh:acme/shop: ")
    assert not Path("shop.html").exists()
    assert not cache.exists()  # refused before anything was fetched

    code = cli.main(["view", "gh:acme/shop/examples/nothing.py", "--export", "shop.html"])
    assert code == 2
    assert "acme/shop has no 'examples/nothing.py' at commit" in capsys.readouterr().err
