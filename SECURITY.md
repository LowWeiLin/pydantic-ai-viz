# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately through GitHub: open the repository's [Security tab](https://github.com/LowWeiLin/pydantic-ai-viz/security) and choose "Report a vulnerability". Include the version (`pydantic-ai-viz --version`), your operating system, and the steps to reproduce.

You should get a reply within seven days. If the report is confirmed, a fix is released first and the advisory is published after it, with credit to you unless you ask otherwise.

## Supported versions

Only the latest release receives security fixes.

## Threat model

pydantic-ai-viz is a local development tool. It is built for one developer on one machine and has no authentication. Do not treat it as a service.

### What it does to limit exposure

- **`view` reads code and never runs it.** `pydantic-ai-viz view` and `pviz.analyze` parse source files with Python's `ast` module. They do not import, `exec`, `eval` or compile the code they read, a test fails if the analyser ever calls one of these, and every step is bounded in size, depth and time, so a hostile file ends in a notice rather than a hang or a crash. This is the default: running code is always a separate, explicit step (`--import`, `run`). Start it with the `pydantic-ai-viz` command or `uvx`: `python -m pydantic_ai_viz` run with an unreviewed repository as the current directory imports that repository's modules (a planted `json.py`, say) before any code of ours runs, which nothing in this package can prevent. How it works is described in [How view works](https://lowweilin.github.io/pydantic-ai-viz/concepts/how-view-works/#pointing-it-at-code-you-do-not-trust).
- **GitHub targets are fetched, never built.** For `view gh:owner/repo` (or a `https://github.com/...` URL), `git` fetches one commit into a fresh repository in the cache directory, writes out only its `.py` files and project files such as `pyproject.toml`, writes symbolic links as plain files and removes its `.git` directory afterwards. No hook, build step or script of the repository runs, `git` is never left waiting for a password, and `--import` is refused for a fetched target.
- **Loopback only by default.** The server binds `127.0.0.1`. Other machines on your network cannot reach it.
- **Host header check.** A request whose `Host` header is not `localhost` or a loopback address (`127.0.0.1`, `[::1]`, or another address in `127.0.0.0/8`) is refused with `403`. This blocks DNS-rebinding attacks, where a web page you visit tries to read the local server through a hostname it controls.
- **No cross-origin access.** The server sends no CORS headers, so a page on another origin cannot read its responses. The one `POST` endpoint also rejects a request whose `Origin` is not the server's own.
- **Nothing is written to disk** unless you ask for a recording or an export. Captured runs live in the memory of your process and go away when it exits.
- **Nothing is sent over the network.** There is no telemetry. Spans go to a private OpenTelemetry tracer provider inside your process, not to a collector. The viewer loads no fonts, scripts or styles from other hosts, and its Content Security Policy forbids it.
- **Captured content is rendered as text,** never as HTML, so a prompt or tool result cannot inject markup into the viewer. Data embedded in an HTML export is escaped so that it cannot close its `<script>` element.

### What you are responsible for

- **Recordings and exports contain your data.** A `.pviz.jsonl` recording or an exported HTML file holds the prompts, instructions, tool arguments, tool results and model output of every captured run, in plain text. Treat such a file like a log file that may contain secrets or personal data. Use `include_content=False` (or `pydantic-ai-viz run --no-content`) to capture structure, timing and token counts without content.
- **Binding a non-loopback host removes the protection.** `serve(host="0.0.0.0")` makes the server reachable by anyone who can reach the machine, without authentication, and turns off the Host header check. A `UserWarning` is issued. Do this only on a network you trust.
- **Other local users and processes.** Anything that can connect to `127.0.0.1` on your machine can read the session. On a shared machine, prefer `record()` and an export over `serve()`.
- **`run` and `view --import` execute code.** `pydantic-ai-viz run script.py` runs the script, and `pydantic-ai-viz view --import TARGET` imports the target to find its agents, which runs its top-level code and that of everything it imports. Only point them at code you trust, as you would with `python script.py`. Plain `pydantic-ai-viz view TARGET` (and `pviz.analyze`) only parses the code: it does not import or execute it, so it is safe to use on code you have not reviewed.
- **The network, for a GitHub target.** `view gh:owner/repo` runs `git` with your git configuration and credential helpers to fetch from `https://github.com`. Point it at repositories you are willing to download.
- **Opening someone else's export.** An export is an HTML file with inline JavaScript. It makes no network requests and its captured data is treated as text, but it is still a file from another person: open only exports from people you trust, or load their `.pviz.jsonl` recording in your own viewer with `pydantic-ai-viz open`.

A way to make `view` or `analyze` execute any part of the code it reads, write outside its cache directory, or hang or crash on a file, is a vulnerability: please report it as described above.

### Out of scope

- Attacks that need a non-loopback `host`, or an attacker who already runs code as your user.
- The security of the agents, tools and models you visualise. pydantic-ai-viz observes your tools; it does not sandbox them.
