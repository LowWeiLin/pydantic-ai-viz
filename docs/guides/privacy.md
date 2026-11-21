# Privacy

Agent runs contain prompts, customer data, tool results and sometimes credentials. This page says where that data goes when you use pydantic-ai-viz, and how to keep it out of the tool altogether.

The short answer: it stays in the memory of your process, and reaches your browser over a loopback connection. Nothing is written to disk and nothing is sent to anyone unless you export a file and send it yourself.

## Where your data goes

| Place | What is there | For how long |
|---|---|---|
| Your process's memory | The event log: blueprints and captured runs | Until the session closes or the process exits |
| Your browser tab | A copy of the event log, received from the local server | Until you close the tab |
| A file on disk | A recording or an export | Only if you ask for one: `session.save`, `session.export_html`, `--record`, `--export`, or the viewer's Export menu |
| The cache directory | The `.py` files of a GitHub repository you named as a `view` target | Until you delete them |
| The network | Nothing, except the `git` fetch of a GitHub repository you named as a `view` target | |

There is no telemetry, no account and no cloud service. Spans go to a private OpenTelemetry tracer provider inside your process, not to a collector, and no global OpenTelemetry state is set. The viewer is a single HTML file that loads no scripts, fonts or styles from other hosts; its Content Security Policy forbids it.

`pydantic-ai-viz open` writes an export to your system's temporary directory in order to open it. That file stays there until your system cleans the directory.

## Reading source code with `view`

`pydantic-ai-viz view` and `pviz.analyze` read your files on your machine and run none of them, so no agent runs, no model is called and no prompt of a user is involved. What they produce describes your code, not your data, but it does quote the code:

- **Prompts written in the source appear in full**: instruction strings, docstrings that become tool descriptions, and default values of tool parameters.
- **Computed values appear as the code that computes them**, for example `os.getenv("MODEL", "openai:gpt-5.2")`, which is shown as the variable `MODEL` with the default the source gives. The value of an environment variable is never read, so a secret held in one is not shown; a secret written as a literal in the code is, as it would be in any copy of the code.
- **File paths are relative** to the directory that was read, so a blueprint export does not carry the layout of your machine. When targets share no directory (a GitHub repository next to a local file, or two drives), each file is named from its own target's project under a short label, such as `owner/repo@abc1234/src/app.py`; an absolute path is never shown.

A blueprint-only export (`view ... --export`) holds no run data, but read it before you share it if your prompts are confidential.

A GitHub target (`gh:owner/repo`, or a `https://github.com/...` URL) is the one case where `view` uses the network: `git` fetches that commit's `.py` files, project files and `SKILL.md` files from GitHub, using your git configuration, and keeps them in a cache directory (`PYDANTIC_AI_VIZ_CACHE_DIR`, or your user cache directory). Nothing is sent but the request for the repository, and a commit already in the cache is read without the network.

## Capture without content

```python
session = pviz.serve(agent, include_content=False)
```

```bash
pydantic-ai-viz run --no-content my_script.py
```

With content capture off, you still see the full shape of every run, but not what was said:

| Kept | Left out |
|---|---|
| Blueprints: tools, schemas, static instructions | Prompts and model responses |
| Which model requests and tool calls happened, in what order, nested how | Resolved instructions of each request |
| Start time, duration and status of every step | Tool arguments and tool results |
| Token counts | The final output of each run |
| Tool names and tool-call ids in the conversation | The message and stack trace of an error (its type is kept) |
| Retries and errors as such | Graph node inputs and the graph output |

In the viewer, the places where content would be show "Content capture is off (include_content=False)".

This is the setting to use when you want to share a recording of a run against real data, or to demonstrate a flow whose prompts are confidential.

Keep in mind:

- **It is decided at capture time.** The content is never recorded, so it cannot leak later. For the same reason, a recording made with content cannot be redacted afterwards. Capture the run again.
- **Blueprints are not redacted.** Static instructions, system prompts, tool descriptions and schemas are part of the blueprint and are still shown. So are the skills of a `Skills` capability: each skill's description, when to use it and the body of its `SKILL.md`, whatever `include_content` is. Skill paths are relative to the workspace or the folder read, never absolute. They describe your code, not your data, but check them before sharing.
- **Token streaming is off.** A token is content, so `stream=True` (or `--stream`) has no effect when content capture is off. See [Token streaming](token-streaming.md#streaming-and-content-capture).
- **With `instrument=False` your own settings apply.** If you instrument Pydantic AI yourself or through Logfire, the `include_content` of that configuration decides what the spans contain. See [Use with Logfire](logfire.md).

## The local server

`serve` starts an HTTP server so your browser can show the session. It is built for one developer on one machine:

- **It binds `127.0.0.1`.** Other machines on your network cannot connect.
- **It checks the `Host` header.** A request that does not address the server as `localhost` or by a loopback address (`127.0.0.1`, `[::1]`, or another address in `127.0.0.0/8`) gets a `403`. This stops a web page you happen to visit from reading the server through a hostname that it points at your machine (DNS rebinding).
- **It sends no CORS headers,** so a page on another origin cannot read its responses, and its one `POST` endpoint rejects requests from other origins.
- **It has no authentication.** Any program running as any user on your machine can connect to a loopback port. On a shared machine, use `record` and an export in place of `serve`.

!!! danger "Binding another host"

    `serve(host="0.0.0.0")` makes the session, with all its prompts and tool results, readable by anyone who can reach the machine, without a password, and turns the `Host` check off. Python issues a `UserWarning` when you do this. To look at a session on a remote machine, prefer an SSH tunnel to its loopback port:

    ```bash
    ssh -L 8765:127.0.0.1:8765 you@remote-host
    ```

## Recordings and exports

A `.pviz.jsonl` recording or an HTML export is the event log in a file: everything in the "Kept" and "Left out" columns above, unless content capture was off. It is plain text and not encrypted.

- Do not commit recordings of real runs to a public repository.
- Do not attach an export to a public issue without reading it first.
- An export with `include_content=False`, or a blueprint-only export (`pydantic-ai-viz view ... --export`), is the safer thing to share.

[Export and share](export-and-share.md#before-you-share) has a short checklist.

## What the viewer does with content

Everything captured is rendered as text, never as HTML, so a prompt or a tool result cannot inject markup or scripts into the page. When data is embedded in an export, it is escaped so that it cannot break out of its container.

The viewer stores three display preferences in your browser, in `localStorage`: your light or dark theme choice, and the width and the collapsed state of the inspector panel. It stores no run data.

## How much is kept

The event log holds at most `max_events` events (50,000 by default). Past that, the oldest finished runs are dropped. Any single string longer than 32,768 characters is cut. Both limits bound memory use; neither is a privacy control.

To report a security problem, see the [security policy](https://github.com/LowWeiLin/pydantic-ai-viz/blob/main/SECURITY.md).
