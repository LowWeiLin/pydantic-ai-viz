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

- **Loopback only by default.** The server binds `127.0.0.1`. Other machines on your network cannot reach it.
- **Host header check.** A request whose `Host` header is not a loopback name (`127.0.0.1`, `localhost` or `[::1]`) is refused with `403`. This blocks DNS-rebinding attacks, where a web page you visit tries to read the local server through a hostname it controls.
- **No cross-origin access.** The server sends no CORS headers, so a page on another origin cannot read its responses. The one `POST` endpoint also rejects a request whose `Origin` is not the server's own.
- **Nothing is written to disk** unless you ask for a recording or an export. Captured runs live in the memory of your process and go away when it exits.
- **Nothing is sent over the network.** There is no telemetry. Spans go to a private OpenTelemetry tracer provider inside your process, not to a collector. The viewer loads no fonts, scripts or styles from other hosts, and its Content Security Policy forbids it.
- **Captured content is rendered as text,** never as HTML, so a prompt or tool result cannot inject markup into the viewer. Data embedded in an HTML export is escaped so that it cannot close its `<script>` element.

### What you are responsible for

- **Recordings and exports contain your data.** A `.pviz.jsonl` recording or an exported HTML file holds the prompts, instructions, tool arguments, tool results and model output of every captured run, in plain text. Treat such a file like a log file that may contain secrets or personal data. Use `include_content=False` (or `pydantic-ai-viz run --no-content`) to capture structure, timing and token counts without content.
- **Binding a non-loopback host removes the protection.** `serve(host="0.0.0.0")` makes the server reachable by anyone who can reach the machine, without authentication, and turns off the Host header check. A warning is printed. Do this only on a network you trust.
- **Other local users and processes.** Anything that can connect to `127.0.0.1` on your machine can read the session. On a shared machine, prefer `record()` and an export over `serve()`.
- **`view` and `run` execute code.** `pydantic-ai-viz view path/to/file.py` imports the file to find its agents, and `pydantic-ai-viz run script.py` runs the script. Only point them at code you trust, as you would with `python script.py`.
- **Opening someone else's export.** An export is an HTML file with inline JavaScript. It makes no network requests and its captured data is treated as text, but it is still a file from another person: open only exports from people you trust, or load their `.pviz.jsonl` recording in your own viewer with `pydantic-ai-viz open`.

### Out of scope

- Attacks that need a non-loopback `host`, or an attacker who already runs code as your user.
- The security of the agents, tools and models you visualise. pydantic-ai-viz observes your tools; it does not sandbox them.
