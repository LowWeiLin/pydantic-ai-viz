"""Wire contract (``pviz: 1``).

These models are the source of truth for everything that crosses the Python/viewer
boundary: the live SSE stream, ``.pviz.jsonl`` recordings and embedded HTML exports.
``schema/pviz.schema.json`` is generated from them by ``scripts/gen_schema.py``.

Rules that hold for every model here:

* every field is required; "unknown" or "not applicable" is an explicit ``None``;
* unknown fields are rejected (``extra="forbid"``), so producers cannot drift silently;
* times are epoch milliseconds as floats.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, TypeAdapter

WIRE_VERSION: Literal[1] = 1
"""Value of ``session.started.pviz``."""

MAX_STRING_CHARS = 32_768
"""Strings longer than this are cut by the normaliser (wire rule 5)."""

TraceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
SpanId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]
SessionId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]


class WireModel(BaseModel):
    """Base class: closed models, no protected ``model_`` namespace."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())


# ---------------------------------------------------------------- blueprints

Provenance: TypeAlias = Literal["code", "object", "observed"]
"""How a fact was obtained.

* ``"code"``: read from source code by static analysis; nothing was imported or run.
* ``"object"``: read from a live Python object (an ``Agent`` or a ``Graph``).
* ``"observed"``: seen happening in a run.
"""

SessionMode: TypeAlias = Literal["capture", "blueprint"]
"""``"capture"``: runs can appear in this session. ``"blueprint"``: it holds blueprints only, for ever."""


class SourceLocation(WireModel):
    """Where something is defined in analysed source code."""

    file: str
    """Path relative to the analysed root, with forward slashes."""
    line: int = Field(ge=1)


class EnvironmentValue(WireModel):
    """A value the code reads from an environment variable when it runs (``os.getenv("X", default)``)."""

    variable: str
    """The name of the environment variable."""
    default: str | None
    """The default the source gives, used only when the variable is not set; ``None`` when it gives none.

    It is never the value: the environment the code runs in decides that.
    """


class ModelInfo(WireModel):
    id: str | None
    name: str
    provider: str | None
    offline: bool
    expression: str | None
    """Source text of the model argument when it is not a string literal.

    ``name`` then repeats it, unless the source builds one of Pydantic AI's model classes
    with a literal model name (``OpenAIChatModel("gpt-5.2", ...)``): that name is the ``name``.
    """
    model_class: str | None
    """The Pydantic AI model class the model is an instance of (``OpenAIChatModel``), when that is known.

    Static analysis knows it when the source constructs one of Pydantic AI's model classes.
    """
    env: EnvironmentValue | None
    """Set when the model name is read from an environment variable when the code runs.

    The name then is the one in the environment: ``name`` and ``expression`` hold the source text,
    and ``model_class`` the class built with that name, if one is.
    """


class Instruction(WireModel):
    kind: Literal["instructions", "system_prompt"]
    dynamic: bool
    text: str | None
    """The literal text; for a template (a dynamic f-string or ``str.format`` call), the template.

    A template is written in the syntax of ``str.format``: each part filled in when the code runs
    is a placeholder in braces (``{date}``), and a literal brace is doubled (``{{``). A placeholder
    whose value the source gives is already filled in.
    """
    function: str | None
    expression: str | None
    """Source text of a value that is neither a literal, an f-string nor a named function."""
    location: SourceLocation | None


class Toolset(WireModel):
    id: str
    toolset_id: str | None
    label: str
    kind: Literal["agent", "function", "mcp", "dynamic", "other"]
    wrappers: list[str]
    opaque: bool
    expression: str | None
    """Source text of a toolset that source code builds in a way static analysis cannot read."""
    location: SourceLocation | None


class Tool(WireModel):
    name: str
    toolset: str
    description: str | None
    parameters_schema: JsonValue
    return_schema: JsonValue
    kind: Literal["function", "output", "external", "unapproved"]
    takes_ctx: bool | None
    is_async: bool | None
    max_retries: int | None
    function: str | None
    origin: Provenance
    location: SourceLocation | None
    library_version: str | None
    """Set for a tool that Pydantic AI itself defines (``duckduckgo_search_tool()``), read by static
    analysis from the source of the installed Pydantic AI: the version of that installation.

    The project may use another version, whose tool can differ.
    """


class OutputInfo(WireModel):
    mode: str
    allows_text: bool | None
    type_name: str | None
    json_schema: JsonValue
    tool_names: list[str]
    location: SourceLocation | None
    """Where the output type is defined."""


CapabilityCategory: TypeAlias = Literal[
    "harness",
    "environment",
    "tools",
    "integration",
    "web",
    "reasoning",
    "context",
    "knowledge",
    "safety",
    "self_extension",
    "runtime",
    "loop",
    "custom",
]
"""The group of the Pydantic AI capabilities docs a capability is listed in, in the docs' order.

``"custom"`` for every other capability: the project's own, another package's, or one this release does not know.
"""


class SkillInfo(WireModel):
    """One skill a harness ``Skills`` capability offers, read from its ``SKILL.md``."""

    name: str
    """The ``name`` of its frontmatter, or else its directory's name: the id ``load_capability`` takes."""
    description: str
    when_to_use: str | None
    model_invocable: bool
    """``False`` when its frontmatter has ``disable-model-invocation: true``: it is left out of the model's catalog."""
    path: str
    """Its ``SKILL.md``, with forward slashes, relative to the workspace it was read from (from a live object) or
    to the analysed root (from source). Never absolute."""
    body: str
    """The Markdown after the frontmatter, as written: what the model gets when it loads the skill."""


class HookInfo(WireModel):
    """One function a ``Hooks`` capability registers."""

    point: str
    """Where it hooks in: the capability method it stands for, as ``Hooks`` keys it (``"before_model_request"``,
    ``"wrap_run"``, ``"on_run_error"``)."""
    function: str | None
    """``module.qualname`` of the function; ``None`` when the source does not name one."""
    tools: list[str] | None
    """For a tool hook, the tools it is limited to (``tools=``), sorted; ``None`` when it runs for every tool."""
    timeout: float | None
    location: SourceLocation | None
    """Where the function is defined. Static analysis only."""


class CapabilityInfo(WireModel):
    name: str
    """The class name; for a capability static analysis cannot name, the source text that builds it."""
    builtin: bool
    """One that Pydantic AI adds to every agent (``_compat.BUILTIN_CAPABILITIES``)."""
    category: CapabilityCategory
    package: Literal["pydantic_ai", "pydantic_ai_harness"] | None
    """Where the class itself is defined, when that is Pydantic AI or the Pydantic AI harness; ``None`` otherwise."""
    deferred: bool
    """Given ``defer_loading=True``: the model loads it on demand."""
    details: dict[str, JsonValue]
    """What the capability was given, by parameter name: values only, secrets and defaults left out."""
    expressions: dict[str, str]
    """Parameters the source computes at run time: name -> source text. Static analysis only; ``{}`` otherwise."""
    toolsets: list[str]
    """``Toolset.id`` of each toolset of this blueprint that the capability gives the agent."""
    native_tools: list[str]
    """Kinds of the provider-side tools the capability gives the agent, for example ``"web_search"``."""
    capability_id: str | None
    """The id Pydantic AI knows the capability by, which ``load_capability`` takes for one loaded on demand.

    From source, only an ``id=`` the source spells out.
    """
    stack: str | None
    """The harness stack (``"Coder"``, ``"Researcher"``) that put the capability on the agent; ``None`` when the
    agent was given it otherwise. Read from a live object only: from source it is ``None``."""
    skills: list[SkillInfo]
    """For a harness ``Skills`` capability, the skills it offers, in the order of the model's catalog; ``[]`` for
    every other capability."""
    hooks: list[HookInfo]
    """For a ``Hooks`` capability, the functions it registers, in the order it runs them for each point; ``[]`` for
    every other capability."""


class AgentSettings(WireModel):
    model_settings: dict[str, JsonValue] | None
    end_strategy: str | None
    tool_retries: int | None
    output_retries: int | None
    tool_timeout: float | None
    expressions: dict[str, str]
    """Settings that source code computes at run time: setting name -> source text.

    The key is a field of this model, or ``model_settings.<key>`` for one model setting.
    The field itself is ``None`` (or the key is absent from ``model_settings``).
    """
    defaults: list[str]
    """Settings (``end_strategy``, ``tool_retries``, ``output_retries``, ``tool_timeout``) whose value is the one
    Pydantic AI gives an agent that is not given it. Read from a live object only: from source it is ``[]``."""


class Delegate(WireModel):
    tool_name: str
    agent_id: str
    evidence: Provenance


class AgentBlueprint(WireModel):
    """Structure of one agent, read from code, from a live object, or from its runs."""

    id: str
    name: str | None
    label: str = Field(min_length=1)
    description: str | None
    source: Provenance
    location: SourceLocation | None
    model: ModelInfo | None
    deps_type: str | None
    instructions: list[Instruction]
    toolsets: list[Toolset]
    tools: list[Tool]
    native_tools: list[str]
    output: OutputInfo
    capabilities: list[CapabilityInfo]
    settings: AgentSettings
    delegates: list[Delegate]
    warnings: list[str]


class GraphNode(WireModel):
    id: str
    kind: str
    label: str | None
    note: str | None
    location: SourceLocation | None


class GraphEdge(WireModel):
    source: str
    target: str
    label: str | None


class GraphBlueprint(WireModel):
    """Structure of one pydantic-graph ``Graph``, read from code or from a live object."""

    id: str
    name: str | None
    label: str = Field(min_length=1)
    source: Literal["code", "object"]
    location: SourceLocation | None
    state_type: str | None
    input_type: str | None
    output_type: str | None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    mermaid: str | None
    warnings: list[str]


# --------------------------------------------------------------------- spans


class TextPart(WireModel):
    type: Literal["text"]
    content: str | None


class ThinkingPart(WireModel):
    type: Literal["thinking"]
    content: str | None


class ToolCallPart(WireModel):
    type: Literal["tool_call"]
    id: str | None
    name: str
    arguments: JsonValue


class ToolResultPart(WireModel):
    type: Literal["tool_result"]
    id: str | None
    name: str
    result: JsonValue


class OtherPart(WireModel):
    type: Literal["other"]
    raw: JsonValue


Part: TypeAlias = Annotated[
    TextPart | ThinkingPart | ToolCallPart | ToolResultPart | OtherPart,
    Field(discriminator="type"),
]


class Message(WireModel):
    role: Literal["system", "user", "assistant", "tool"]
    parts: list[Part]


class Usage(WireModel):
    input_tokens: int
    output_tokens: int


class SpanError(WireModel):
    type: str
    message: str | None
    stacktrace: str | None


class RetryInfo(WireModel):
    cause: str
    attempt: int
    max_retries: int


Deferral: TypeAlias = Literal["approval", "external"]
"""Why a tool call was left for the caller: it needs approval, or it runs outside the agent."""


class DeferredCall(WireModel):
    """A tool call a run ended waiting on (its output was ``DeferredToolRequests``)."""

    call_id: str | None
    tool_name: str
    kind: Deferral


class AgentRunData(WireModel):
    model_name: str | None
    conversation_id: str | None
    instructions: str | None
    output: JsonValue
    usage: Usage | None
    messages: list[Message] | None
    deferred: list[DeferredCall]
    """Empty unless the run ended waiting for approvals or external results."""


class ModelRequestData(WireModel):
    model_name: str
    provider: str | None
    response_model: str | None
    settings: dict[str, JsonValue]
    instructions: str | None
    input_messages: list[Message] | None
    output_messages: list[Message] | None
    usage: Usage | None


class ToolCallData(WireModel):
    tool_name: str
    call_id: str | None
    arguments: JsonValue
    result: JsonValue
    retry: RetryInfo | None
    deferral: Deferral | None
    """Set when the tool raised ``ApprovalRequired`` or ``CallDeferred`` instead of returning."""


class GraphRunData(WireModel):
    graph_id: str
    graph_name: str
    inputs: JsonValue
    output: JsonValue


class GraphNodeData(WireModel):
    graph_id: str
    node_id: str
    inputs: JsonValue


class RunEvent(WireModel):
    """A ``CapabilityEvent`` or ``CustomEvent`` of a run, recorded by ``pydantic_ai_viz.CaptureEvents``."""

    kind: str
    """The event's kind (``"planning.plan_updated"``); for a custom event, its name."""
    event_kind: Literal["capability", "custom"]
    capability_id: str | None
    """The id of the capability that emitted it, when one did."""
    tool_call_id: str | None
    tool_name: str | None
    ts_ms: float
    data: JsonValue
    """The event's own fields; ``None`` when content is not captured."""


class SpanBase(WireModel):
    trace_id: TraceId
    span_id: SpanId
    parent_id: SpanId | None
    name: str
    start_ms: float
    end_ms: float | None
    status: Literal["running", "ok", "error"]
    agent_name: str | None
    run_id: str | None
    error: SpanError | None
    attributes: dict[str, JsonValue]
    events: list[RunEvent]
    """The events recorded in this span, in order; ``[]`` until it ends."""


class AgentRunSpan(SpanBase):
    kind: Literal["agent_run"]
    data: AgentRunData


class ModelRequestSpan(SpanBase):
    kind: Literal["model_request"]
    data: ModelRequestData


class ToolCallSpan(SpanBase):
    kind: Literal["tool_call"]
    data: ToolCallData


class GraphRunSpan(SpanBase):
    kind: Literal["graph_run"]
    data: GraphRunData


class GraphNodeSpan(SpanBase):
    kind: Literal["graph_node"]
    data: GraphNodeData


class OtherSpan(SpanBase):
    kind: Literal["other"]
    data: None


Span: TypeAlias = Annotated[
    AgentRunSpan | ModelRequestSpan | ToolCallSpan | GraphRunSpan | GraphNodeSpan | OtherSpan,
    Field(discriminator="kind"),
]


# ------------------------------------------------------------------ envelope


class EnvelopeBase(WireModel):
    seq: int = Field(ge=0)
    ts: float


class Versions(WireModel):
    pydantic_ai_viz: str
    pydantic_ai: str
    python: str


class SessionStarted(EnvelopeBase):
    type: Literal["session.started"]
    pviz: Literal[1]
    session_id: SessionId
    title: str
    mode: SessionMode
    include_content: bool
    stream: bool
    notices: list[str]
    """Messages about the session as a whole, such as files static analysis had to skip."""
    versions: Versions


class AgentBlueprintEvent(EnvelopeBase):
    type: Literal["blueprint.agent"]
    agent: AgentBlueprint


class GraphBlueprintEvent(EnvelopeBase):
    type: Literal["blueprint.graph"]
    graph: GraphBlueprint


class SpanStarted(EnvelopeBase):
    type: Literal["span.started"]
    span: Span


class SpanEnded(EnvelopeBase):
    type: Literal["span.ended"]
    span: Span


class StreamDelta(EnvelopeBase):
    type: Literal["stream.delta"]
    trace_id: TraceId
    span_id: SpanId
    part_index: int = Field(ge=0)
    part_kind: Literal["text", "thinking", "tool_call"]
    tool_name: str | None
    delta: str


Envelope: TypeAlias = Annotated[
    SessionStarted | AgentBlueprintEvent | GraphBlueprintEvent | SpanStarted | SpanEnded | StreamDelta,
    Field(discriminator="type"),
]

ENVELOPE_ADAPTER: TypeAdapter[Envelope] = TypeAdapter(Envelope)
SPAN_ADAPTER: TypeAdapter[Span] = TypeAdapter(Span)

SCHEMA_ID = "https://lowweilin.github.io/pydantic-ai-viz/schema/pviz.schema.json"


def parse_envelope(line: str | bytes) -> Envelope:
    """Validate one recording line (a JSON envelope) strictly.

    Strict JSON mode accepts an integer where a float is expected, but never
    coerces strings to numbers or numbers to booleans.
    """
    return ENVELOPE_ADAPTER.validate_json(line, strict=True)


def to_wire(model: WireModel) -> dict[str, Any]:
    """Dump a wire model as a JSON-ready dict with every field present."""
    return model.model_dump(mode="json")


def _drop_discriminators(node: Any) -> Any:
    """Remove the OpenAPI ``discriminator`` keyword, which is not JSON Schema.

    Each ``oneOf`` branch pins its tag with ``const``, so validation is unchanged,
    and strict validators (ajv) accept the result without extra options.
    """
    if isinstance(node, dict):
        items: dict[str, Any] = node  # pyright: ignore[reportUnknownVariableType]
        return {
            key: _drop_discriminators(value)
            for key, value in items.items()
            if not (key == "discriminator" and "oneOf" in items)
        }
    if isinstance(node, list):
        return [_drop_discriminators(value) for value in node]  # pyright: ignore[reportUnknownVariableType]
    return node


def envelope_json_schema() -> dict[str, Any]:
    """JSON Schema (draft 2020-12) for one envelope."""
    schema: dict[str, Any] = _drop_discriminators(ENVELOPE_ADAPTER.json_schema())
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "pviz envelope (wire format version 1)",
        "description": (
            "One event of a pydantic-ai-viz session log: an SSE message, a line of a .pviz.jsonl "
            "recording, or an element of an embedded export. Generated from "
            "src/pydantic_ai_viz/_models.py by scripts/gen_schema.py; do not edit by hand."
        ),
        **schema,
    }
