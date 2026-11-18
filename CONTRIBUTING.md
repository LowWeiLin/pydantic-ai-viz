# Contributing

Thanks for helping. Bug reports, fixes, docs improvements and new ideas are all welcome.

For anything larger than a bug fix, please [open an issue](https://github.com/LowWeiLin/pydantic-ai-viz/issues) first so the approach can be agreed before you write the code. By taking part you agree to follow the [Code of Conduct](https://github.com/LowWeiLin/pydantic-ai-viz/blob/main/CODE_OF_CONDUCT.md). Report security problems privately, as described in the [security policy](https://github.com/LowWeiLin/pydantic-ai-viz/blob/main/SECURITY.md).

## How the repository is laid out

| Path | What lives there |
|---|---|
| `src/pydantic_ai_viz/` | The Python package: introspection, capture, session, server, export, CLI |
| `src/pydantic_ai_viz/demos/` | The offline demos |
| `tests/` | Python tests (`tests/demos/` for the demos, `tests/integration/` for cross-language checks) |
| `web/` | The viewer: React, TypeScript, Vite. It builds to one file, `web/dist/viewer.html` |
| `schema/pviz.schema.json` | JSON Schema of the wire format, generated from the Python models |
| `scripts/` | `gen_schema.py` and `build_demo_site.py` |
| `examples/` | Short runnable examples |
| `docs/`, `mkdocs.yml` | The documentation site |

The Python models in `src/pydantic_ai_viz/_models.py` are the source of truth for the [wire format](https://lowweilin.github.io/pydantic-ai-viz/reference/wire-format/). The schema is generated from them, and the TypeScript types in `web/src/wire.ts` are checked against the schema in tests.

## Set up

You need:

- [uv](https://docs.astral.sh/uv/) for Python. It installs the right Python version for you.
- [Node.js](https://nodejs.org/) 22 or newer and [pnpm](https://pnpm.io/), only if you touch the viewer in `web/`.

```bash
git clone https://github.com/LowWeiLin/pydantic-ai-viz
cd pydantic-ai-viz
uv sync
uv run pydantic-ai-viz --version
```

The viewer is a build output and is not committed. Without it, the Python package serves a placeholder page that tells you to build the UI, and the Python tests run against that placeholder. To get the real viewer:

```bash
pnpm --dir web install
pnpm --dir web build
```

The build writes `web/dist/viewer.html` and copies it to `src/pydantic_ai_viz/_static/viewer.html`, where the Python package picks it up.

Always run Python through `uv run`, so the project's environment is used.

## Checks to run before you open a pull request

Run the block for each area you changed. CI runs all of them.

### Python

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q
```

`uv run ruff format .` and `uv run ruff check --fix .` fix most lint findings. The formatter also formats Python code blocks in Markdown files, so a docs change can fail `ruff format --check`; the same command fixes it.

All tests are offline: they use Pydantic AI's `TestModel` and `FunctionModel`, never a real provider. Keep it that way. A test must not need an API key or a network connection.

### Viewer

```bash
pnpm --dir web lint
pnpm --dir web typecheck
pnpm --dir web test
pnpm --dir web build
pnpm --dir web exec playwright install chromium   # first time only
pnpm --dir web e2e
```

For development, `pnpm --dir web dev` starts Vite with a sample recording loaded. To develop against a live Python session, start one (for example `uv run pydantic-ai-viz demo --no-open`) and open the Vite URL with `?live` appended; Vite proxies `/api` to `http://127.0.0.1:8765`.

### Demos and integration

```bash
uv run pytest tests/demos -q
uv run pydantic-ai-viz demo --list
uv run pydantic-ai-viz demo support --speed 0 --record support.pviz.jsonl
uv run python scripts/build_demo_site.py --out site/demo
uv run pytest tests/integration -q
```

`tests/integration` runs real processes: the command line, the examples and the demo server. Two of its tests need the built viewer, and one of those also needs node and a Playwright Chromium, because it opens the live demo in a browser. Without them these tests are skipped. Set `PVIZ_REQUIRE_BROWSER_TESTS=1` to turn a skip into a failure, as the CI end-to-end job does.

To run the viewer's Playwright specs over the real demo recordings as well as the fixtures, point them at the demo site:

```bash
PVIZ_RECORDINGS_DIR="$PWD/site/demo/recordings" PVIZ_SCREENSHOTS_DIR="$PWD/web/test-results/screenshots" pnpm --dir web e2e
```

### Older Pydantic AI versions

The lock file pins the newest Pydantic AI the project was tested with; the declared floor is older. To run the suite against another release without touching the lock file:

```bash
uv run --with "pydantic-ai-slim==2.60.0" pytest -q
```

If you raise or lower the floor in `pyproject.toml`, run the suite against the new floor this way and update the table in `docs/reference/compatibility.md` with what you ran. Never declare a floor that was not run.

### Docs

```bash
uv sync --group docs
uv run mkdocs build --strict
uv run mkdocs serve   # live preview at http://127.0.0.1:8000
```

## Regenerating generated files

### The wire schema

If you change `src/pydantic_ai_viz/_models.py`, regenerate the schema and commit it with the change:

```bash
uv run python scripts/gen_schema.py
git diff --exit-code schema/
```

CI fails when the committed schema differs from what the models generate. A change to the wire format also needs matching changes in `web/src/wire.ts`, the fixtures in `web/fixtures/`, and the [wire format page](https://lowweilin.github.io/pydantic-ai-viz/reference/wire-format/). Adding a field is compatible. Removing or renaming one is a breaking change and needs a new `pviz` version.

### The docs screenshots

The screenshots in `docs/assets/screenshots/` are generated by Playwright from real demo recordings. Do not edit them by hand. To regenerate them after a UI change:

```bash
pnpm --dir web build
uv run python scripts/build_demo_site.py --out site/demo
```

Then run the screenshot script with `PVIZ_RECORDINGS_DIR` set to the absolute path of `site/demo/recordings`:

```bash
# bash
PVIZ_RECORDINGS_DIR="$PWD/site/demo/recordings" pnpm --dir web screenshots
```

```powershell
# PowerShell
$env:PVIZ_RECORDINGS_DIR = "$PWD\site\demo\recordings"; pnpm --dir web screenshots
```

Commit the changed PNG files in their own commit (`docs: update screenshots`).

## Commits

The project uses [Conventional Commits](https://www.conventionalcommits.org/). The subject line is imperative, at most 72 characters, in the form `type(scope): summary`. Add a short body when the reason for the change is not obvious from the subject.

- **Types:** `feat`, `fix`, `docs`, `test`, `refactor`, `build`, `ci`, `chore`.
- **Scopes:** `core`, `introspect`, `capture`, `server`, `cli`, `web`, `demos`, `docs`, `ci`. The scope is optional.

```text
fix(capture): keep tool spans when the parent run was evicted

The reducer dropped a span.ended event whose span.started had been
evicted, so long sessions lost the tail of their oldest run.
```

Keep commits small and atomic:

- Each commit leaves the checks for its area green.
- Tests go in the same commit as the code they cover.
- Stage explicit paths (`git add <paths>`), not `git add -A` or `git commit -a`, so unrelated files do not slip in.
- Do not mix a refactor with a behaviour change.

## Pre-commit hooks

The hooks are optional and catch most CI failures before you push:

```bash
uv run pre-commit install --install-hooks
```

They run ruff, the ruff formatter, whitespace and end-of-file fixers, Biome over `web/` when a file there changes (this one needs `pnpm --dir web install` to have been run), and a commit-message check for the Conventional Commits format. To run them over the whole repository: `uv run pre-commit run --all-files`.

## Pull requests

- Branch from `main`, keep the pull request to one topic, and fill in the template.
- Add a line to the "Unreleased" section of `CHANGELOG.md` for any change a user would notice.
- Update the docs in the same pull request when behaviour, a signature or a CLI flag changes.
- Pull requests are squash-merged, so the pull request title must itself be a Conventional Commit subject.

## Working with Pydantic AI internals

Some blueprint fields have no public accessor in Pydantic AI and are read from private attributes. Two rules keep that safe:

- All private access goes through `_compat.get_private(...)`. A missing attribute must produce a warning on the blueprint, never an exception.
- Version-sensitive span handling (attribute names, message shapes) lives in `_normalize.py` only. The viewer never interprets raw OpenTelemetry attributes.

If you add a private access, add it to the compat contract test and to the table on the [compatibility page](https://lowweilin.github.io/pydantic-ai-viz/reference/compatibility/).

## Releasing (maintainers)

Two things are set up once per repository, outside the code. Without them the workflows fail:

- **GitHub Pages.** In the repository's Settings > Pages > Build and deployment, set Source to "GitHub Actions". The Docs workflow deploys on every push to `main`; until this is set its deploy job fails and the documentation URL answers 404.
- **PyPI trusted publishing.** On PyPI, add a trusted publisher for the project: owner `LowWeiLin`, repository `pydantic-ai-viz`, workflow `release.yml`, environment `pypi`. The Release workflow publishes with it; no API token is stored in the repository.

For each release:

1. Update `src/pydantic_ai_viz/_version.py`.
2. In `CHANGELOG.md`, rename "Unreleased" to the new version with today's date and start a new empty "Unreleased" section.
3. Commit as `chore: release X.Y.Z`, tag `vX.Y.Z` and push the tag.

The release workflow builds the viewer and the wheel, checks that the viewer is inside the wheel, publishes to PyPI with trusted publishing, and creates a GitHub release from the changelog section.
