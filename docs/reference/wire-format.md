# Wire format

A pydantic-ai-viz session is an append-only log of events. The same events travel over the live connection, fill a `.pviz.jsonl` recording and sit inside an HTML export. This page specifies them.

- **Name and version:** `pviz`, version `1`. The version is the `pviz` field of the first event.
- **Machine-readable schema:** [`schema/pviz.schema.json`](https://github.com/LowWeiLin/pydantic-ai-viz/blob/main/schema/pviz.schema.json) (JSON Schema 2020-12), generated from the Python models. It describes one event.
- **Source of truth:** the Pydantic models in `src/pydantic_ai_viz/_models.py`.

The types below are written in TypeScript notation because it is compact. `Json` means any JSON value.

## Rules for every event { #rules }

1. **Every listed field is always present.** A value that is unknown or does not apply is `null`, never left out.
2. **Readers ignore what they do not know:** unknown fields, and events with an unknown `type`. This is what lets a newer writer work with an older reader.
3. **Times are milliseconds since the Unix epoch,** as JSON numbers with a fractional part (microsecond precision). The envelope's `ts` and a span's `start_ms` and `end_ms` are on the same clock.
4. **Ids are lowercase hex:** `trace_id` is 32 characters, `span_id` and `parent_id` are 16.
5. **Long strings are cut.** Any string longer than 32,768 characters is truncated and ends with `... [truncated, N chars total]`.
6. **Content can be absent.** When the session's `include_content` is `false`, content-bearing fields are `null`.

## Envelope and events { #events }

Each event is one JSON object: an envelope with a sequence number and a timestamp, plus the fields of one event type.

```ts
type Envelope = { seq: number; ts: number } & Event;

type Event =
  | SessionStarted
  | { type: "blueprint.agent"; agent: AgentBlueprint }   // upsert by agent.id
  | { type: "blueprint.graph"; graph: GraphBlueprint }   // upsert by graph.id
  | { type: "span.started"; span: Span }                 // status "running", end_ms null
  | { type: "span.ended"; span: Span }                   // the full span; replaces by span_id
  | StreamDelta;

type SessionStarted = {
  type: "session.started";
  pviz: 1;
  session_id: string;          // 32 hex characters, new for every session
  title: string;
  include_content: boolean;
  stream: boolean;             // token streaming enabled; always false when include_content is false
  versions: { pydantic_ai_viz: string; pydantic_ai: string; python: string };
};

type StreamDelta = {
  type: "stream.delta";
  trace_id: string;
  span_id: string;             // the open model_request span this delta belongs to
  part_index: number;          // index of the response part within this model request
  part_kind: "text" | "thinking" | "tool_call";
  tool_name: string | null;    // set for tool_call parts
  delta: string;               // text to append, or a fragment of the arguments JSON for tool_call
};
```

### Ordering and identity { #ordering }

- `seq` starts at 0 and increases by 1 per event within a session. Gaps appear only after [eviction](#eviction).
- `session.started` is always `seq: 0` and is never evicted.
- For one span, `span.started` comes before `span.ended`. A parent's `span.started` comes before its children's.
- A `stream.delta` for a span lies between that span's `span.started` and `span.ended`.
- Blueprint events can arrive at any time and are upserts by `id`. At most one blueprint has any given non-null `name`.
- A reader must accept a `span.ended` with no earlier `span.started`. This happens after eviction, or when capture was attached in the middle of a run.
- A **run** is a trace: all spans that share a `trace_id`. Its root is the span with `parent_id: null`, an `agent_run` or a `graph_run`.

## Blueprints { #blueprints }

```ts
type AgentBlueprint = {
  id: string;                  // opaque, stable within a session: "a1", "a2", ...
  name: string | null;         // agent.name; null until known
  label: string;               // never empty: the name, else the variable name, else "agent-<n>"
  description: string | null;
  source: "static" | "observed";
  model: { id: string | null; name: string; provider: string | null; offline: boolean } | null;
  deps_type: string | null;    // qualified type name; null for object or None
  instructions: Instruction[];
  toolsets: Toolset[];
  tools: Tool[];               // function and external tools; output tools are in output.tool_names
  native_tools: string[];      // provider-side tool kinds, for example "tool_search"
  output: {
    mode: string;              // "text" | "auto" | "tool" | "native" | "prompted" | "unknown"
    allows_text: boolean | null;
    type_name: string | null;  // for example "Resolution", "str", "A | B"
    json_schema: Json | null;
    tool_names: string[];      // for example ["final_result"]; may be filled from observed runs
  };
  capabilities: { name: string; builtin: boolean }[];
  settings: {
    model_settings: { [k: string]: Json } | null;
    end_strategy: string | null;
    tool_retries: number | null;
    output_retries: number | null;
    tool_timeout: number | null;
  };
  delegates: { tool_name: string; agent_id: string; evidence: "static" | "observed" }[];
  warnings: string[];          // human-readable notes about anything that could not be read
};

type Instruction = {
  kind: "instructions" | "system_prompt";
  dynamic: boolean;
  text: string | null;         // null for dynamic functions
  function: string | null;     // qualified function name for dynamic ones
};

type Toolset = {
  id: string;                  // unique within the blueprint: "ts0", "ts1", ...
  toolset_id: string | null;   // Pydantic AI's toolset id: "<agent>", a user-supplied id, or null
  label: string;               // "Agent tools", "FunctionToolset 'kb'", ...
  kind: "agent" | "function" | "mcp" | "dynamic" | "other";
  wrappers: string[];          // class names of wrapper toolsets around it, outermost first
  opaque: boolean;             // true when its tools cannot be listed without running it
};

type Tool = {
  name: string;
  toolset: string;             // a Toolset.id
  description: string | null;
  parameters_schema: Json;
  return_schema: Json | null;
  kind: "function" | "output" | "external" | "unapproved";
  takes_ctx: boolean | null;
  is_async: boolean | null;
  max_retries: number | null;  // null means "inherit the agent's default"
  function: string | null;     // qualified function name
  origin: "static" | "observed";
};

type GraphBlueprint = {
  id: string;                  // "g1", "g2", ...
  name: string | null;
  label: string;
  state_type: string | null;
  input_type: string | null;
  output_type: string | null;
  nodes: { id: string; kind: string; label: string | null; note: string | null }[];
  edges: { source: string; target: string; label: string | null }[];
  mermaid: string | null;      // graph.render(direction="LR")
  warnings: string[];
};
```

- Known graph node kinds are `"start"`, `"end"`, `"step"`, `"decision"`, `"join"`, `"broadcast"` and `"map"`. A reader draws an unknown kind as a step.
- `model.offline` is `true` when the provider is `test` or `function`.
- `source`, `origin` and `evidence` are explained in [Blueprint vs run](../concepts/blueprint-vs-run.md).

## Spans { #spans }

```ts
type SpanBase = {
  trace_id: string;
  span_id: string;
  parent_id: string | null;
  name: string;                // display name, see "Normalisation"
  start_ms: number;
  end_ms: number | null;       // null while running
  status: "running" | "ok" | "error";
  agent_name: string | null;   // owning agent; null for graph spans
  run_id: string | null;       // Pydantic AI's run id of the owning agent run
  error: { type: string; message: string | null; stacktrace: string | null } | null;
  attributes: { [k: string]: Json };  // leftover raw OpenTelemetry attributes; {} on span.started
};

type Span = SpanBase & (
  | { kind: "agent_run";     data: AgentRunData }
  | { kind: "model_request"; data: ModelRequestData }
  | { kind: "tool_call";     data: ToolCallData }
  | { kind: "graph_run";     data: GraphRunData }
  | { kind: "graph_node";    data: GraphNodeData }
  | { kind: "other";         data: null }
);

type Usage = { input_tokens: number; output_tokens: number };

type AgentRunData = {
  model_name: string | null;
  conversation_id: string | null;
  instructions: string | null;       // resolved instructions; end only
  output: Json | null;               // final result; end only
  usage: Usage | null;               // this agent's own requests only; end only
  messages: Message[] | null;        // the full conversation; end only
};

type ModelRequestData = {
  model_name: string;
  provider: string | null;           // end only
  response_model: string | null;     // end only
  settings: { [k: string]: Json };   // request settings such as temperature; {} at start
  instructions: string | null;       // end only
  input_messages: Message[] | null;  // end only
  output_messages: Message[] | null; // end only
  usage: Usage | null;               // end only
};

type ToolCallData = {
  tool_name: string;
  call_id: string | null;
  arguments: Json | null;            // available at start
  result: Json | null;               // end only
  retry: { cause: string; attempt: number; max_retries: number } | null;
};

type GraphRunData  = { graph_id: string; graph_name: string; inputs: Json | null; output: Json | null };
type GraphNodeData = { graph_id: string; node_id: string; inputs: Json | null };

type Message = { role: "system" | "user" | "assistant" | "tool"; parts: Part[] };
type Part =
  | { type: "text";        content: string | null }
  | { type: "thinking";    content: string | null }
  | { type: "tool_call";   id: string | null; name: string; arguments: Json | null }
  | { type: "tool_result"; id: string | null; name: string; result: Json | null }
  | { type: "other";       raw: Json };
```

"End only" fields are `null` in `span.started` and filled in `span.ended`.

### Reading spans correctly { #reading-spans }

- **Retry or error.** A `tool_call` with `status: "error"` and a non-null `retry` is a retry: the tool asked the model to try again. Only an error with `retry: null` is a failure.
- **Token totals.** A run's total is the sum of `usage` over the `model_request` spans of the trace. Do not add `agent_run` usages together; see [why](../concepts/how-capture-works.md#why-token-totals-come-from-model-requests).
- **Output tools.** A call to an output tool such as `final_result` has no span. It appears in `messages` only. An `agent_run` that ends with `status: "ok"` produced its output.
- **Delegation.** An `agent_run` whose parent is a `tool_call` is a delegated run, started by that tool.
- **Graph nesting.** An `agent_run` started inside a graph step has that `graph_node` as its parent, except in a parallel batch, where its parent is the `graph_run`.

## Normalisation { #normalisation }

Spans are produced by Pydantic AI as OpenTelemetry spans and converted to the shapes above in Python. Readers never need the raw attributes; they are documented here so the conversion can be checked.

**Kind and name**

| Condition on the raw span | `kind` | `name` |
|---|---|---|
| attribute `pydantic_ai_viz.kind` is `graph_run` or `graph_node` | the same | graph name or node id |
| `gen_ai.operation.name` is `invoke_agent` | `agent_run` | the agent name |
| `gen_ai.operation.name` is `chat` | `model_request` | the span name without its `chat ` prefix |
| `gen_ai.operation.name` is `execute_tool` | `tool_call` | `gen_ai.tool.name` |
| anything else | `other` | the raw span name |

**Fields**

| Wire field | Raw attribute |
|---|---|
| `agent_name` | `gen_ai.agent.name`, else `agent_name` |
| `run_id` | `gen_ai.agent.call.id` |
| `AgentRunData.model_name` | `model_name` |
| `AgentRunData.conversation_id` | `gen_ai.conversation.id` |
| `AgentRunData.output` | `final_result` |
| `AgentRunData.usage` | `gen_ai.aggregated_usage.input_tokens`, `.output_tokens` |
| `AgentRunData.messages` | `pydantic_ai.all_messages` |
| `instructions` (agent run and model request) | `gen_ai.system_instructions`, a JSON list whose `content` values are joined with a blank line |
| `ModelRequestData.model_name` | `gen_ai.request.model`, else the span name |
| `ModelRequestData.provider` | `gen_ai.provider.name`, else `gen_ai.system` |
| `ModelRequestData.response_model` | `gen_ai.response.model` |
| `ModelRequestData.settings` | every `gen_ai.request.*` attribute except `model`, keyed by its suffix |
| `ModelRequestData.input_messages`, `output_messages` | `gen_ai.input.messages`, `gen_ai.output.messages` |
| `ModelRequestData.usage` | `gen_ai.usage.input_tokens`, `.output_tokens` |
| `ToolCallData.call_id`, `arguments`, `result` | `gen_ai.tool.call.id`, `.arguments`, `.result` |
| `ToolCallData.retry` | `pydantic_ai.tool.retry.cause`, `.attempt`, `.max_retries` |
| `error` | the span's `exception` event (`exception.type`, `.message`, `.stacktrace`) |
| `GraphRunData`, `GraphNodeData` | `pydantic_ai_viz.graph.id`, `.name`, `.node_id`, `.inputs`, `.output` |

**Other rules**

- **Status.** OpenTelemetry `UNSET` and `OK` become `"ok"`, `ERROR` becomes `"error"`, and a span that has not ended is `"running"`. An `ERROR` status without an exception event gives `error: {type: "Error", message: <status description>, stacktrace: null}`.
- **JSON inside strings.** The final result, tool arguments and tool results arrive as strings. They are parsed as JSON; if that fails, the string is kept as it is.
- **Messages.**
    - `tool_call_response` parts become `tool_result`.
    - `reasoning` and `thinking` parts both become `thinking`.
    - A part of an unknown type becomes `{type: "other", raw}`.
    - A message whose parts are all `tool_result` gets the role `"tool"`, whichever role the instrumentation version gave it.
    - Tool-call `arguments` that arrive as a JSON string (streamed requests) are parsed.
- **`attributes`** holds every raw attribute that was not mapped above, except `model_request_parameters`, `gen_ai.tool.definitions` and `logfire.json_schema`, which are large and already represented in the blueprint.

## Recording { #recording }

A recording is a UTF-8 text file with the extension `.pviz.jsonl`. Each line is one envelope as compact JSON. The first line is the `session.started` event. The file is exactly the session's event log, in order.

```text
{"seq":0,"ts":1795046400000.0,"type":"session.started","pviz":1,"session_id":"5f0c2a9e6f3b4d0c9a7e1b2c3d4e5f60","title":"Support triage","include_content":true,"stream":true,"versions":{"pydantic_ai_viz":"0.1.0","pydantic_ai":"2.70.0","python":"3.12.9"}}
{"seq":1,"ts":1795046400001.2,"type":"blueprint.agent","agent":{"id":"a1","name":"triage","label":"triage", ...}}
{"seq":2,"ts":1795046401000.0,"type":"span.started","span":{"trace_id":"0af7651916cd43dd8448eb211c80319c","span_id":"b7ad6b7169203331","parent_id":null,"kind":"agent_run","name":"triage","start_ms":1795046401000.0,"end_ms":null,"status":"running", ...}}
```

(The second and third lines are shortened here with `...`; real lines are complete objects.)

## Example events { #example }

A tool call that asks for a retry. At start, the arguments are known:

```json
{
  "seq": 8, "ts": 1795046401805.0, "type": "span.started",
  "span": {
    "trace_id": "0af7651916cd43dd8448eb211c80319c",
    "span_id": "53995c3f42cd8ad8",
    "parent_id": "b7ad6b7169203331",
    "kind": "tool_call", "name": "get_order",
    "start_ms": 1795046401805.0, "end_ms": null, "status": "running",
    "agent_name": "triage", "run_id": "01a1ec72-21e1-753d-baa9-89ecf9dfa6b4",
    "error": null, "attributes": {},
    "data": {
      "tool_name": "get_order", "call_id": "call_1",
      "arguments": {"order_id": "1042"}, "result": null, "retry": null
    }
  }
}
```

At end, the same span carries its result, the error and the retry information:

```json
{
  "seq": 9, "ts": 1795046402100.0, "type": "span.ended",
  "span": {
    "trace_id": "0af7651916cd43dd8448eb211c80319c",
    "span_id": "53995c3f42cd8ad8",
    "parent_id": "b7ad6b7169203331",
    "kind": "tool_call", "name": "get_order",
    "start_ms": 1795046401805.0, "end_ms": 1795046402100.0, "status": "error",
    "agent_name": "triage", "run_id": "01a1ec72-21e1-753d-baa9-89ecf9dfa6b4",
    "error": {
      "type": "pydantic_ai.exceptions.ToolRetryError",
      "message": "Order ids look like 'A-1042'.",
      "stacktrace": "Traceback ..."
    },
    "attributes": {"gen_ai.operation.name": "execute_tool", "logfire.msg": "running tool: get_order"},
    "data": {
      "tool_name": "get_order", "call_id": "call_1",
      "arguments": {"order_id": "1042"},
      "result": "Order ids look like 'A-1042'.",
      "retry": {"cause": "model_retry", "attempt": 1, "max_retries": 2}
    }
  }
}
```

A streamed fragment of a tool call's arguments:

```json
{
  "seq": 6, "ts": 1795046401390.0, "type": "stream.delta",
  "trace_id": "0af7651916cd43dd8448eb211c80319c", "span_id": "00f067aa0ba902b7",
  "part_index": 1, "part_kind": "tool_call", "tool_name": "get_order",
  "delta": "{\"order_id\": "
}
```

With `include_content: false`, the events have the same shape, with `content`, `arguments`, `result`, `output` and `instructions` set to `null`.

## Live transport { #transport }

`serve` runs an HTTP server on `127.0.0.1`. All paths are relative, so the viewer also works behind a path prefix.

| Method and path | Response |
|---|---|
| `GET /` | `200 text/html`: the viewer |
| `GET /api/v1/health` | `200 {"ok": true, "pviz": 1, "version": "0.1.0", "session_id": "..."}` |
| `GET /api/v1/events` | `200 text/event-stream`: the event log, see below |
| `GET /api/v1/export.jsonl` | `200 application/x-ndjson`: the recording, as an attachment named `<title>.pviz.jsonl` |
| `GET /api/v1/export.html` | `200 text/html`: a single-file export of the current log, as an attachment |
| `POST /api/v1/rerun` | Demo mode only. `202 {"accepted": true}`, or `409 {"accepted": false, "reason": "running"}`. `404` outside demo mode. |
| anything else | `404 {"error": "not found"}` |

Every response carries `Cache-Control: no-store`. No CORS headers are sent. A request whose `Host` header is not a loopback name gets `403`, unless the server was started on a non-loopback host. `POST` also requires the `Origin` header to be absent or the server's own. A request body over 64 KiB gets `413`, and a `Content-Length` that is not a plain number gets `400`.

### Event stream { #sse }

```text
retry: 1000

id: 5f0c2d9e8a7b4c1d9e3f2a1b0c4d5e6f:0
data: {"seq":0,"ts":1795046400000.0,"type":"session.started",...}

id: 5f0c2d9e8a7b4c1d9e3f2a1b0c4d5e6f:1
data: {"seq":1,...}

: ping
```

- One Server-Sent Event per envelope, with the default event name. `id` is `<session_id>:<seq>`.
- On a connection without `Last-Event-ID`, the server sends every retained event in order and then follows the log. There is no separate snapshot endpoint.
- With `Last-Event-ID: <session_id>:n`, the server sends retained events with `seq > n`. If the session id is not the server's own, the server has restarted since the client last heard from it, and it sends everything from the start. It does the same for an id it cannot parse and for an `n` beyond its highest `seq`.
- A `: ping` comment is sent every 15 seconds.
- **Reader rule:** on a `session.started` whose `session_id` differs from the current one, discard all state and start over.

### Eviction { #eviction }

When the log exceeds `max_events` (50,000 by default), the oldest completed traces are dropped, each as a whole. `session.started` and blueprint events are never dropped. Neither is a trace with a span still open, nor the trace that completed last, so a log can hold more than `max_events` events. Evicted events leave gaps in `seq`.

## HTML export { #html-export }

An export is the viewer page with the event log embedded. The viewer contains one placeholder element:

```html
<script id="pviz-boot" type="application/json">"__PVIZ_BOOT__"</script>
```

To make an export, the quoted token is replaced by a JSON object:

```ts
type Boot =
  | { mode: "live"; events_url: "api/v1/events"; export_html_url: string; export_jsonl_url: string; rerun_url: string | null }
  | { mode: "embedded"; events: Envelope[] };
```

In the embedded JSON, `<`, `>` and `&` are written as `<`, `>` and `&`, and U+2028 and U+2029 are escaped, so captured content cannot end the script element.

When the placeholder has not been replaced, the viewer runs in **static mode** and looks for data in this order:

1. `?live` in the URL: connect to `api/v1/events` (used for frontend development).
2. `?src=<URL on the same origin>`: fetch that recording.
3. `recordings/index.json` next to the page: show a gallery of the recordings it lists.
4. Otherwise: show a drop zone that accepts a `.pviz.jsonl` file.

```ts
// recordings/index.json
type RecordingIndex = { recordings: { id: string; title: string; description: string; src: string }[] };
```

## Compatibility of the format { #versioning }

The wire format is versioned separately from the package. Within `pviz: 1`:

- fields and event types may be **added**; readers ignore what they do not know,
- nothing is removed or renamed, and no field changes its meaning.

A change that breaks those rules gets a new `pviz` number and is called out in the [changelog](../changelog.md).
