"""The real-world manifest and the script that reports on it, without the network.

The projects themselves are only fetched by ``test_realworld.py``, under the
``network`` marker. Here the manifest is checked as a document, and the script is
run against a stand-in for GitHub: a bare repository on this machine.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import realworld_report
from realworld_report import Expectation, ManifestError, Project, Result, check, load_manifest, main, measure, render

from pydantic_ai_viz import _github, analyze

AGENTS = '''
from pathlib import Path

from pydantic_ai import Agent

Path("ran.txt").write_text("the fetched code was executed")

helper = Agent("openai:gpt-5.2", name="helper")
lead = Agent(pick_model(), name="lead")


@lead.tool_plain
async def ask_helper(question: str) -> str:
    """Ask the helper."""
    return (await helper.run(question)).output
'''

ENTRY = """
[[project]]
id = "shop"
repo = "acme/shop"
sha = "{sha}"
subdir = "src"
licence = "MIT"
exercises = "Two agents, one of which asks the other."

[project.expect]
min_files = 1
min_agents = 2
min_tools = 1
min_delegations = 1
agents = ["lead", "helper"]
tools = ["ask_helper"]
delegations = ["lead -> helper"]
"""


# ------------------------------------------------------------ the manifest


def test_the_manifest_pins_eight_to_twelve_licensed_projects_to_full_commits() -> None:
    projects = load_manifest()
    assert 8 <= len(projects) <= 12
    for project in projects:
        assert re.fullmatch(r"[0-9a-f]{40}", project.sha), project.id
        assert project.licence not in ("", "NOASSERTION"), project.id
        assert project.exercises.endswith("."), project.id
        assert project.target == f"gh:{project.repo}@{project.sha}" + (f"/{project.subdir}" if project.subdir else "")
        assert project.url.startswith(f"https://github.com/{project.repo}/tree/{project.sha}")
        # Every entry expects something by name, or it would pass on any repository at all.
        expect = project.expect
        assert expect.min_files >= 1
        assert expect.min_agents >= 1, project.id
        assert expect.agents or expect.tools or expect.graphs, project.id
    assert len({(project.repo, project.sha, project.subdir) for project in projects}) == len(projects)


def test_the_set_covers_the_ways_pydantic_ai_is_used() -> None:
    expectations = [project.expect for project in load_manifest()]
    assert sum(expect.min_tools > 0 for expect in expectations) >= 6
    assert sum(expect.min_delegations > 0 for expect in expectations) >= 3
    assert sum(expect.min_graphs > 0 for expect in expectations) >= 3
    assert sum(expect.min_files >= 100 for expect in expectations) >= 3  # whole applications, not only scripts


def test_the_docs_page_lists_every_project_of_the_manifest_and_nothing_else() -> None:
    page = (Path(__file__).resolve().parents[2] / "docs" / "real-world.md").read_text(encoding="utf-8")
    projects = load_manifest()
    # The command each row offers runs as written, and pins the same commit as the manifest.
    offered = re.findall(r"`(gh:[^`]+)`", page)
    assert sorted(set(offered)) == sorted(project.target for project in projects)
    # The results table has one row per project, linked to the commit it was made at.
    rows = re.findall(r"^\| [^|]+ \| \[`([0-9a-f]{7})`\]\((\S+)\) \|", page, flags=re.MULTILINE)
    assert rows == [(project.sha[:7], project.url) for project in projects]


def manifest(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "manifest.toml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (('sha = "' + "a" * 40 + '"', 'sha = "main"'), "sha must be a full 40-character lower-case commit SHA"),
        (('sha = "' + "a" * 40 + '"', 'sha = "' + "A" * 40 + '"'), "sha must be a full 40-character"),
        (('licence = "MIT"', 'licence = ""'), "'licence' is missing or empty"),
        (('licence = "MIT"\n', ""), "'licence' is missing or empty"),
        (('repo = "acme/shop"', 'repo = "https://github.com/acme/shop"'), "is not owner/name"),
        (('subdir = "src"', 'subdir = "../elsewhere"'), "is not a path inside the repository"),
        (("min_agents = 2", 'min_agents = "two"'), "min_agents must be a whole number"),
        (("min_agents = 2", "min_agent = 2"), "unknown keys in expect: ['min_agent']"),
        (('id = "shop"', 'id = "shop"\nbranch = "main"'), "unknown keys ['branch']"),
        (('tools = ["ask_helper"]', 'tools = "ask_helper"'), "tools must be a list of names"),
        (('delegations = ["lead -> helper"]', 'delegations = ["lead helper"]'), "must read 'from -> to'"),
    ],
)
def test_a_manifest_entry_that_is_not_usable_is_refused_with_the_reason(
    tmp_path: Path, change: tuple[str, str], message: str
) -> None:
    text = ENTRY.format(sha="a" * 40)
    assert change[0] in text
    with pytest.raises(ManifestError) as raised:
        load_manifest(manifest(tmp_path, text.replace(*change)))
    assert message in str(raised.value)


def test_a_manifest_that_is_empty_broken_or_repeats_an_id_is_refused(tmp_path: Path) -> None:
    entry = ENTRY.format(sha="a" * 40)
    for text, message in (
        ("", "no [[project]] entries"),
        ("[[project]\n", "manifest.toml: "),
        (entry + entry, "the id 'shop' is used twice"),
    ):
        with pytest.raises(ManifestError) as raised:
            load_manifest(manifest(tmp_path, text))
        assert message in str(raised.value)
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "missing.toml")


# ------------------------------------------------------- checking a result


def test_an_analysis_is_checked_against_every_part_of_an_expectation(tmp_path: Path) -> None:
    (tmp_path / "agents.py").write_text(AGENTS, encoding="utf-8")
    found = analyze(tmp_path / "agents.py")

    met = Expectation(
        min_agents=2,
        min_tools=1,
        min_delegations=1,
        agents=("lead", "helper"),
        tools=("ask_helper",),
        delegations=("lead->helper",),  # spaces around the arrow do not matter
    )
    assert check(found, met) == []

    unmet = Expectation(
        min_files=2,
        min_agents=3,
        min_tools=2,
        min_graphs=1,
        min_delegations=2,
        agents=("lead", "manager"),
        tools=("ask_manager",),
        graphs=("pipeline",),
        delegations=("helper -> lead",),
    )
    assert check(found, unmet) == [
        "1 files found, at least 2 expected",
        "2 agents found, at least 3 expected",
        "1 tools found, at least 2 expected",
        "0 graphs found, at least 1 expected",
        "1 delegations found, at least 2 expected",
        "agents not found: manager",
        "tools not found: ask_manager",
        "graphs not found: pipeline",
        "delegations not found: helper -> lead",
    ]


def test_an_analysis_that_was_cut_short_does_not_pass(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"agent_{index}.py").write_text("from pydantic_ai import Agent\na = Agent('test')\n", "utf-8")
    from pydantic_ai_viz import Limits

    found = analyze(tmp_path, limits=Limits(max_files=2))
    (problem,) = check(found, Expectation())
    assert problem.startswith("the analysis was cut short: ")
    # A file of the project that is not valid Python is the project's business, and no failure of the analysis.
    (tmp_path / "agent_2.py").write_text("def broken(:\n", encoding="utf-8")
    assert check(analyze(tmp_path), Expectation(min_agents=2)) == []


# ----------------------------------------------------------- the report


def test_results_are_written_as_a_table_with_a_line_of_totals() -> None:
    first = Project("a", "acme/shop", "a" * 40, "src", "MIT", "Shop.", Expectation())
    second = Project("b", "acme/big", "b" * 40, "", "MIT", "Big.", Expectation())
    gone = Project("c", "acme/gone", "c" * 40, "", "MIT", "Gone.", Expectation())
    table = render(
        [
            Result(first, files=3, agents=2, tools=1, graphs=0, delegations=1, warnings=1, seconds=0.04),
            Result(second, files=1204, agents=51, tools=160, graphs=2, delegations=0, warnings=40, seconds=8.26),
            Result(gone, problems=["could not be read: git could not fetch acme/gone: not | found\nat all"]),
        ]
    )
    assert table.splitlines() == [
        "| Project | Commit | Files | Agents | Tools | Graphs | Delegation edges | Warnings | Time |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| acme/shop `src` | [`aaaaaaa`](https://github.com/acme/shop/tree/{'a' * 40}/src) | 3 | 2 | 1 | 0 | 1 | 1 "
        "| 0.0 s |",
        f"| acme/big | [`bbbbbbb`](https://github.com/acme/big/tree/{'b' * 40}) | 1,204 | 51 | 160 | 2 | 0 | 40 "
        "| 8.3 s |",
        f"| acme/gone | [`ccccccc`](https://github.com/acme/gone/tree/{'c' * 40}) | failed: could not be read: git "
        "could not fetch acme/gone: not \\| found at all | | | | | | |",
        "| **2 projects** | | **1,207** | **53** | **161** | **2** | **1** | **41** | **8.3 s** |",
    ]
    assert table.endswith("|\n")


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A stand-in for github.com holding ``acme/shop``, and an empty cache. Returns the commit of the shop."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "src" / "agents.py").write_text(AGENTS, encoding="utf-8", newline="\n")
    (work / "README.md").write_text("# shop\n", encoding="utf-8")

    def git(*arguments: str, cwd: Path = work) -> str:
        identity = ["-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]
        done = subprocess.run(["git", *identity, *arguments], cwd=cwd, capture_output=True, text=True, check=True)
        return done.stdout.strip()

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "first")
    sha = git("rev-parse", "HEAD")
    bare = tmp_path / "remote" / "acme" / "shop.git"
    bare.parent.mkdir(parents=True)
    git("clone", "-q", "--bare", str(work), str(bare))
    git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=bare)
    git("config", "uploadpack.allowFilter", "true", cwd=bare)
    monkeypatch.setattr(_github, "GITHUB", (tmp_path / "remote").as_uri())
    monkeypatch.setenv(_github.CACHE_VARIABLE, str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)
    return sha


def test_the_script_fetches_each_project_reads_it_and_writes_the_table(
    hub: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    listed = manifest(tmp_path, ENTRY.format(sha=hub))
    out = tmp_path / "results" / "table.md"
    assert main(["--manifest", str(listed), "--out", str(out)]) == 0

    row = out.read_text(encoding="utf-8").splitlines()[2]
    cells = [cell.strip() for cell in row.strip("|").split("|")]
    assert cells[:8] == [
        "acme/shop `src`",
        f"[`{hub[:7]}`](https://github.com/acme/shop/tree/{hub}/src)",
        "1",  # files
        "2",  # agents
        "1",  # tools
        "0",  # graphs
        "1",  # delegation edges
        "1",  # warnings: the model of ``lead`` is computed
    ]
    assert re.fullmatch(r"\d+\.\d s", cells[8])
    assert "shop: ok (2 agents, " in capsys.readouterr().err
    assert not (tmp_path / "ran.txt").exists(), "the fetched code was executed"

    # Without --out the table goes to standard output, and only the table.
    assert main(["--manifest", str(listed)]) == 0
    assert capsys.readouterr().out.splitlines()[2].startswith("| acme/shop `src` | ")


def test_the_script_fails_when_a_project_falls_short_or_cannot_be_fetched(
    hub: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    short = ENTRY.format(sha=hub).replace("min_agents = 2", "min_agents = 5")
    gone = ENTRY.format(sha="0" * 40).replace('id = "shop"', 'id = "gone"').replace("acme/shop", "acme/gone")
    listed = manifest(tmp_path, short + gone)
    assert main(["--manifest", str(listed)]) == 1
    captured = capsys.readouterr()
    assert "shop: FAILED (2 agents, " in captured.err
    assert "  2 agents found, at least 5 expected" in captured.err
    assert "gone: FAILED" in captured.err
    assert "| acme/gone `src` | " in captured.out
    assert "failed: could not be read: " in captured.out
    assert "**1 projects**" in captured.out

    # One project can be picked out; a name that is not in the manifest is a usage error.
    assert main(["--manifest", str(listed), "--only", "shop"]) == 1
    assert "gone" not in capsys.readouterr().err
    assert main(["--manifest", str(listed), "--only", "shap"]) == 2
    assert "no project with id shap" in capsys.readouterr().err
    assert main(["--manifest", str(tmp_path / "nowhere.toml")]) == 2


def test_measuring_a_project_times_the_reading_and_not_the_fetching(hub: str, monkeypatch: pytest.MonkeyPatch) -> None:
    project = Project("shop", "acme/shop", hub, "src", "MIT", "Shop.", Expectation(min_agents=2))
    fetched: list[str] = []
    real_fetch = _github.fetch

    def fetch(reference: _github.Reference, *, timeout: float = 300.0) -> _github.Checkout:
        fetched.append(reference.slug)
        return real_fetch(reference, timeout=timeout)

    monkeypatch.setattr(realworld_report._github, "fetch", fetch)
    result, found = measure(project)
    assert found is not None
    assert (result.problems, result.files, result.agents, result.tools, result.delegations) == ([], 1, 2, 1, 1)
    assert 0 < result.seconds < 30
    # Once by the script, ahead of the clock, and once by the analysis itself, which finds the commit cached.
    assert fetched == ["acme/shop", "acme/shop"]
