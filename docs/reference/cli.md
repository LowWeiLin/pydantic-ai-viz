# CLI

```text
pydantic-ai-viz demo [NAME] [--list] [--speed X] [--port N] [--no-open]
                     [--record OUT.pviz.jsonl] [--export OUT.html]
pydantic-ai-viz view TARGET... [--import] [--export OUT.html] [--port N] [--no-open]
pydantic-ai-viz run  [--stream] [--no-content] [--record OUT.pviz.jsonl] [--export OUT.html]
                     [--port N] [--no-open] SCRIPT [ARGS...]
pydantic-ai-viz open FILE.pviz.jsonl
pydantic-ai-viz export FILE.pviz.jsonl -o OUT.html
pydantic-ai-viz --version
```

| Command | In one line |
|---|---|
| [`demo`](#demo) | Play a bundled, offline demo |
| [`view`](#view) | Draw the blueprint of agents and graphs from their source code, without running it |
| [`run`](#run) | Run a script and watch its agent runs live |
| [`open`](#open) | Open a recording in the browser |
| [`export`](#export) | Convert a recording to a single HTML file |

The command is installed with the package. `python -m pydantic_ai_viz` is the same program, and `uvx pydantic-ai-viz ...` runs it without installing anything.

## demo { #demo }

```text
pydantic-ai-viz demo [NAME] [--list] [--speed X] [--port N] [--no-open]
                     [--record OUT.pviz.jsonl] [--export OUT.html]
```

Play one of the bundled demos. They use scripted models: no API key, no network.

By default this starts the viewer server, opens your browser, and starts the scenario when the page connects. The server keeps running until ++ctrl+c++; the **Run again** button in the viewer replays the scenario. Token streaming is on.

With `--record` or `--export`, no server is started: the scenario is played once, the files are written, and the command exits.

| Argument | Description |
|---|---|
| `NAME` | Which demo: `support`, `research` or `pipeline`. Defaults to `support`. See [Demos](../demos.md). |
| `--list` | Print the available demos, one `name - description` per line, and exit. |
| `--speed X` | Playback speed multiplier for the scripted delays. Default `1.0`. `2` is twice as fast; `0` removes all delays. |
| `--port N` | Port for the server. Default `8765`. If it is busy, a free port is used. |
| `--no-open` | Do not open a browser. The URL is printed. |
| `--record OUT.pviz.jsonl` | Write a recording, then exit. |
| `--export OUT.html` | Write a single-file HTML export, then exit. |

```bash
pydantic-ai-viz demo
pydantic-ai-viz demo research --speed 2
pydantic-ai-viz demo pipeline --speed 0 --export pipeline.html
```

## view { #view }

```text
pydantic-ai-viz view TARGET... [--import] [--export OUT.html] [--port N] [--no-open]
```

Draw blueprints by **reading source files**. The code is parsed, never imported or executed: nothing in it runs, and nothing it imports has to be installed. What the source spells out is shown as a value; what it computes at run time is shown as the expression that computes it. [How view works](../concepts/how-view-works.md) explains what can and cannot be read this way.

| Argument | Description |
|---|---|
| `TARGET...` | One or more [targets](#targets). |
| `--import` | Import the targets and read the live objects instead of the source. **This executes the target code**; see [below](#view-import). |
| `--export OUT.html` | Write a blueprint-only HTML file and exit, without starting a server. |
| `--port N` | Port for the server. Default `8765`. If it is busy, a free port is used. |
| `--no-open` | Do not open a browser. |

```bash
pydantic-ai-viz view .
pydantic-ai-viz view agents.py
pydantic-ai-viz view src/my_app
pydantic-ai-viz view my_app/agents.py:support_agent
pydantic-ai-viz view my_app/agents.py my_app/pipeline.py --export blueprint.html
pydantic-ai-viz view gh:pydantic/pydantic-ai@v2.70.0/examples
```

`view` reports on stderr what it read, for example `Read 1 file without running any code: 2 agents, 0 graphs`, followed by the number of warnings when the source left something open. A file that had to be skipped (not valid Python, unreadable, too large) is named there too, on a line marked `note:`, and in the viewer. The number of files includes the files read because a target imports them.

### Targets { #targets }

| Form | Meaning |
|---|---|
| `path/to/file.py` | Every agent and graph in that file, including those made inside functions and classes |
| `path/to/file.py:name` | The agent or graph assigned to `name` at the top level of that file, and the agents it delegates to |
| `path/to/directory` | Every agent and graph in every `.py` file below that directory, however deep |
| `pkg.module`, `pkg.module:name` | The file of that module, or every file of that package. It is looked for on disk, below the working directory, its `src` directory and the entries of `sys.path`; nothing is imported to find it |
| `https://github.com/owner/repo`, optionally followed by `/tree/REF/SUBDIR` or `/blob/REF/FILE` | That repository, or the directory or file of it that the URL names, at the branch, tag or commit `REF` (the default branch when there is none) |
| `gh:owner/repo[@REF][/SUBDIR]` | The same, in short |

- Imports between the project's own files are followed: an agent in one file that uses a tool, an output model or a toolset from another, or delegates to an agent imported from another, is linked to it. Installed packages are never opened. A file that is read only because another one imports it adds no blueprints of its own, except for what a target's file knows by name.
- In a directory, virtual environments, `node_modules`, `site-packages`, `__pycache__`, `build`, `dist` and hidden directories are passed over, and links are not followed, to files or to directories, here or when an import is followed. At most 2,000 files are read; when there are more, a note says so.
- A GitHub repository is fetched with `git`, which must be installed: one commit, without history, and of it only the `.py` files. It is kept in a cache directory (`PYDANTIC_AI_VIZ_CACHE_DIR`, or the cache directory of your user account), so a commit is fetched once. A commit is named by its full 40-character SHA. The fetched code is only read; `--import` is refused for it.
- An agent without a `name` is labelled with the variable it is assigned to.
- A target that cannot be read is a usage error (exit code 2): a file, directory or module that does not exist, a repository or `REF` that cannot be fetched, or a `name` no agent or graph is assigned to. So is source in which no agent or graph is found.

### Importing instead: `--import` { #view-import }

```bash
pydantic-ai-viz view --import my_app.agents:support_agent
pydantic-ai-viz view --import my_app.agents my_app.pipeline:graph
```

With `--import`, the targets are loaded and the live `Agent` and `Graph` objects are read. No agent is run and no model is called, but **the code of the target is executed** as any import executes it, so use `--import` only on code you trust. Its dependencies must be installed.

Use it when the source does not say enough: models and tools computed at run time, a graph assembled in a loop, or when you want Pydantic AI's defaults shown.

| Form (with `--import`) | Meaning |
|---|---|
| `pkg.module:attr` | The agent or graph named `attr` in the importable module `pkg.module` |
| `pkg.module` | Every agent and graph defined at the top level of that module |
| `path/to/file.py` | Every agent and graph defined at the top level of that file |
| `path/to/file.py:attr` | The agent or graph named `attr` in that file |

- A module target is imported, so it must be importable from where you run the command: same environment, same working directory or `PYTHONPATH`.
- A file target is executed to find its objects, with `__name__` set to something other than `"__main__"`, so an `if __name__ == "__main__":` block does not run. Code at the top level of the file does run.
- A target that cannot be found is a usage error (exit code 2).

## run { #run }

```text
pydantic-ai-viz run [--stream] [--no-content] [--record OUT.pviz.jsonl] [--export OUT.html]
                    [--port N] [--no-open] SCRIPT [ARGS...]
```

Start the viewer, then run `SCRIPT` as `python SCRIPT ARGS...` would: `__name__` is `"__main__"` and `sys.argv` is `[SCRIPT, *ARGS]`. Every agent run in the script is captured as it happens.

When the script ends, the agents and graphs left in its global namespace are registered, so their full blueprints appear. If `--record` or `--export` was given, the files are written at that point. The server then keeps running until ++ctrl+c++.

If the script raises, the traceback is printed and the server keeps running, so you can inspect the runs that led to the failure.

A script that calls `sys.exit()` is treated the same way: a non-zero code is reported on stderr, the files are still written and the server keeps running. The script's exit code is not passed on; `run` exits with `0` after ++ctrl+c++. For unattended recording, where the process has to end by itself, use [`pviz.record(...)`](python-api.md#record) in code.

If you stop the script with ++ctrl+c++ while it is running (the only way to end one that loops, such as a chat loop or a worker), the `--record` and `--export` files are still written with everything captured up to then, and the command exits.

!!! note "Options go before the script"

    Everything after `SCRIPT` belongs to the script. `pydantic-ai-viz run my_script.py --stream` passes `--stream` to `my_script.py`; `pydantic-ai-viz run --stream my_script.py` turns streaming on.

| Argument | Description |
|---|---|
| `SCRIPT` | Path to a Python file. |
| `ARGS...` | Passed to the script unchanged. |
| `--stream` | Capture tokens as they arrive. This switches model requests to streaming; see [Token streaming](../guides/token-streaming.md). |
| `--no-content` | Capture structure, timing and token counts, but no prompts, arguments or results. Turns `--stream` off as well, because tokens are content. See [Privacy](../guides/privacy.md). |
| `--record OUT.pviz.jsonl` | Write a recording when the script ends. |
| `--export OUT.html` | Write a single-file HTML export when the script ends. |
| `--port N` | Port for the server. Default `8765`. If it is busy, a free port is used. |
| `--no-open` | Do not open a browser. |

```bash
pydantic-ai-viz run my_script.py
pydantic-ai-viz run --stream --record out.pviz.jsonl my_script.py --customer 42
```

## open { #open }

```text
pydantic-ai-viz open FILE.pviz.jsonl
```

Open a recording in your browser. The recording is converted to an HTML export in a temporary directory and opened from there. No server is started, and the command returns at once.

## export { #export }

```text
pydantic-ai-viz export FILE.pviz.jsonl -o OUT.html
```

Convert a recording to a single self-contained HTML file.

| Argument | Description |
|---|---|
| `FILE.pviz.jsonl` | The recording to convert. |
| `-o OUT.html` | Where to write the export. Required. |

## Global options

| Option | Description |
|---|---|
| `--version` | Print `pydantic-ai-viz <version>` and exit. |
| `-h`, `--help` | Show help. Every command has its own: `pydantic-ai-viz run --help`. |

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Runtime error |
| `2` | Usage error: bad arguments, unknown demo, missing file, target not found |

## Behaviour worth knowing

- **Output is plain ASCII,** so it prints correctly on a Windows console. A typical line: `pydantic-ai-viz 0.1.0 - serving http://127.0.0.1:8765 (Ctrl+C to stop)`.
- **The server binds `127.0.0.1`.** The CLI has no option to bind another address; use [`serve(host=...)`](python-api.md#serve) from Python if you need that, and read [Privacy](../guides/privacy.md) first.
- **Pydantic AI's start-up banner is suppressed.** The CLI sets `PYDANTIC_AI_NO_BANNER=1` unless you have set the variable yourself.
- **A busy port is not an error.** The server moves to a free port and prints the URL it really uses.
