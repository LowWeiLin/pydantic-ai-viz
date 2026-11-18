// @vitest-environment node
/**
 * The fixtures are the frontend's stand-in for a backend, so they must be valid wire data.
 * Each line is checked against the JSON Schema generated from the Python models, and the
 * ordering guarantees of the contract are checked directly.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import Ajv2020 from "ajv/dist/2020";
import { describe, expect, test } from "vitest";
import { fixture, fixtureText } from "./test/factory";
import type { Envelope, RecordingIndex, Span } from "./wire";

const webDir = path.resolve(import.meta.dirname, "..");
const fixturesDir = path.join(webDir, "fixtures");
const schemaPath = path.resolve(webDir, "..", "schema", "pviz.schema.json");
const recordings = readdirSync(fixturesDir).filter((name) => name.endsWith(".pviz.jsonl"));
/** Blueprint-only sessions. They hold no run, so they are kept out of the recordings. */
const blueprints = readdirSync(path.join(fixturesDir, "blueprint")).map(
  (name) => `blueprint/${name}`,
);

test("the expected fixtures exist", () => {
  expect(recordings.sort()).toEqual([
    "failed.pviz.jsonl",
    "redacted.pviz.jsonl",
    "sample.pviz.jsonl",
  ]);
  expect(blueprints).toEqual(["blueprint/newsroom.pviz.jsonl"]);
});

const hasSchema = existsSync(schemaPath);
if (!hasSchema) {
  console.warn(
    `\n[pviz] NOTICE: ${schemaPath} not found. Fixture schema validation is SKIPPED.\n` +
      "       Generate it with: uv run python scripts/gen_schema.py\n",
  );
}

describe.skipIf(!hasSchema)("fixtures match schema/pviz.schema.json", () => {
  const validate = hasSchema
    ? new Ajv2020({ allErrors: true, strict: false }).compile(
        JSON.parse(readFileSync(schemaPath, "utf8")),
      )
    : null;

  test.each([...recordings, ...blueprints])("%s", (name) => {
    const problems: string[] = [];
    fixture(name).forEach((envelope, i) => {
      if (validate?.(envelope)) return;
      const detail = (validate?.errors ?? [])
        .filter((e) => e.keyword !== "oneOf" && e.keyword !== "const")
        .slice(0, 4)
        .map((e) => `${e.instancePath || "/"} ${e.message}`)
        .join("; ");
      problems.push(`line ${i + 1} (${envelope.type}): ${detail}`);
    });
    expect(problems).toEqual([]);
  });

  test("the schema rejects a broken envelope (the check is not vacuous)", () => {
    const [first] = fixture("sample.pviz.jsonl");
    expect(validate?.({ ...first, pviz: 2 })).toBe(false);
    expect(validate?.({ ...first, surprise: true })).toBe(false);
  });
});

describe.each(recordings)("%s keeps the ordering guarantees", (name) => {
  const envelopes = fixture(name);
  const spansOf = (type: "span.started" | "span.ended"): Map<string, { seq: number; span: Span }> =>
    new Map(
      envelopes.flatMap((e) =>
        e.type === type ? [[e.span.span_id, { seq: e.seq, span: e.span }] as const] : [],
      ),
    );
  const starts = spansOf("span.started");
  const ends = spansOf("span.ended");

  test("is LF-terminated JSONL that starts with session.started at seq 0", () => {
    const text = fixtureText(name);
    expect(text.endsWith("\n")).toBe(true);
    expect(text.includes("\r")).toBe(false);
    expect(envelopes[0]).toMatchObject({ seq: 0, type: "session.started", pviz: 1 });
    expect(envelopes.filter((e) => e.type === "session.started")).toHaveLength(1);
  });

  test("seq increases by one and ts never goes back", () => {
    envelopes.forEach((envelope, i) => {
      expect(envelope.seq).toBe(i);
      expect(envelope.ts).toBeGreaterThanOrEqual(envelopes[i - 1]?.ts ?? 0);
    });
  });

  test("a span starts before it ends, and after its parent started", () => {
    for (const [id, end] of ends) {
      const start = starts.get(id);
      if (start) expect(start.seq).toBeLessThan(end.seq);
      expect(end.span.end_ms).toBeGreaterThanOrEqual(end.span.start_ms);
      expect(end.span.status).not.toBe("running");
    }
    for (const [, start] of starts) {
      expect(start.span).toMatchObject({ status: "running", end_ms: null, attributes: {} });
      const parent = start.span.parent_id ? starts.get(start.span.parent_id) : undefined;
      if (parent) expect(parent.seq).toBeLessThan(start.seq);
    }
  });

  test("every delta lies between its span's start and end", () => {
    for (const envelope of envelopes) {
      if (envelope.type !== "stream.delta") continue;
      const start = starts.get(envelope.span_id);
      expect(start?.span.kind).toBe("model_request");
      expect(envelope.seq).toBeGreaterThan(start?.seq ?? Number.POSITIVE_INFINITY);
      const end = ends.get(envelope.span_id);
      if (end) expect(envelope.seq).toBeLessThan(end.seq);
    }
  });

  test("blueprint names are unique and every agent span has a blueprint", () => {
    const names = new Map<string, string>();
    for (const envelope of envelopes) {
      if (envelope.type !== "blueprint.agent" || envelope.agent.name === null) continue;
      const owner = names.get(envelope.agent.name);
      expect(owner ?? envelope.agent.id).toBe(envelope.agent.id);
      names.set(envelope.agent.name, envelope.agent.id);
    }
    for (const { span } of [...starts.values(), ...ends.values()]) {
      if (span.agent_name !== null) expect(names.has(span.agent_name)).toBe(true);
    }
  });
});

describe("what each fixture is for", () => {
  const types = (envelopes: Envelope[]) => new Set(envelopes.map((e) => e.type));
  const endedSpans = (envelopes: Envelope[]): Span[] =>
    envelopes.flatMap((e) => (e.type === "span.ended" ? [e.span] : []));

  test("sample: streaming, parallel tools, a retry, delegation, structured output, a graph run", () => {
    const envelopes = fixture("sample.pviz.jsonl");
    const spans = endedSpans(envelopes);
    expect(types(envelopes)).toEqual(
      new Set([
        "session.started",
        "blueprint.agent",
        "blueprint.graph",
        "span.started",
        "span.ended",
        "stream.delta",
      ]),
    );
    const kinds = new Set(
      envelopes.flatMap((e) => (e.type === "stream.delta" ? [e.part_kind] : [])),
    );
    expect(kinds).toEqual(new Set(["text", "thinking", "tool_call"]));

    const retry = spans.find((s) => s.kind === "tool_call" && s.data.retry !== null);
    expect(retry).toMatchObject({ status: "error", name: "get_order" });

    // Three tool calls overlap in time: they were started before any of them ended.
    const firstTools = spans
      .filter((s) => s.kind === "tool_call")
      .sort((a, b) => a.start_ms - b.start_ms)
      .slice(0, 3);
    const earliestEnd = Math.min(...firstTools.map((s) => s.end_ms ?? 0));
    expect(firstTools.every((s) => s.start_ms < earliestEnd)).toBe(true);

    const sub = spans.find((s) => s.kind === "agent_run" && s.name === "refunds");
    const parent = spans.find((s) => s.span_id === sub?.parent_id);
    expect(parent).toMatchObject({
      kind: "tool_call",
      name: "escalate_refund",
      agent_name: "triage",
    });

    const triage = spans.find((s) => s.kind === "agent_run" && s.name === "triage");
    expect(triage?.kind === "agent_run" && triage.data.output).toMatchObject({
      status: "refunded",
    });

    const evidence = envelopes.flatMap((e) =>
      e.type === "blueprint.agent" && e.agent.id === "a1" ? [e.agent.delegates[0]?.evidence] : [],
    );
    expect(evidence).toEqual(["object", "observed"]);

    const nodes = spans.filter((s) => s.kind === "graph_node");
    expect(nodes.map((s) => s.name)).toEqual([
      "write",
      "review",
      "write",
      "review",
      "publish",
      "__end__",
    ]);
    const inStep = spans.find((s) => s.kind === "agent_run" && s.parent_id === nodes[0]?.span_id);
    expect(inStep?.name).toBe("writer");
  });

  test("failed: an error without retry, an observed blueprint, an other span, an open run", () => {
    const envelopes = fixture("failed.pviz.jsonl");
    const spans = endedSpans(envelopes);
    const failedTool = spans.find((s) => s.kind === "tool_call" && s.status === "error");
    expect(failedTool?.kind === "tool_call" && failedTool.data.retry).toBeNull();
    expect(failedTool?.error?.type).toBe("httpx.ConnectTimeout");
    expect(spans.some((s) => s.kind === "agent_run" && s.status === "error")).toBe(true);
    expect(spans.some((s) => s.kind === "other" && s.data === null)).toBe(true);
    expect(
      envelopes.some((e) => e.type === "blueprint.agent" && e.agent.source === "observed"),
    ).toBe(true);
    const startedIds = envelopes.flatMap((e) =>
      e.type === "span.started" ? [e.span.span_id] : [],
    );
    const endedIds = new Set(spans.map((s) => s.span_id));
    expect(startedIds.filter((id) => !endedIds.has(id))).toHaveLength(2);
    expect([...endedIds].filter((id) => !startedIds.includes(id))).toHaveLength(2);
  });

  test("redacted: structure is kept and no content is present", () => {
    const envelopes = fixture("redacted.pviz.jsonl");
    expect(envelopes[0]).toMatchObject({ include_content: false, stream: false });
    expect(types(envelopes).has("stream.delta")).toBe(false);
    for (const span of endedSpans(envelopes)) {
      if (span.kind === "agent_run") {
        expect(span.data).toMatchObject({ output: null, instructions: null });
        const parts = span.data.messages?.flatMap((m) => m.parts) ?? [];
        expect(parts.map((p) => p.type)).toEqual(["text", "tool_call", "tool_result", "text"]);
        for (const part of parts) {
          if (part.type === "text") expect(part.content).toBeNull();
          if (part.type === "tool_call")
            expect(part).toMatchObject({ name: "file_claim", arguments: null });
          if (part.type === "tool_result") expect(part.result).toBeNull();
        }
      }
      if (span.kind === "tool_call")
        expect(span.data).toMatchObject({ arguments: null, result: null });
      if (span.kind === "model_request") expect(span.data.usage).not.toBeNull();
    }
  });

  test("newsroom: read from source, with everything static analysis can and cannot say", () => {
    const envelopes = fixture("blueprint/newsroom.pviz.jsonl");
    expect(fixtureText("blueprint/newsroom.pviz.jsonl")).not.toContain("\r");
    expect(envelopes.map((e) => e.seq)).toEqual(envelopes.map((_, i) => i));
    expect(types(envelopes)).toEqual(
      new Set(["session.started", "blueprint.agent", "blueprint.graph"]),
    );
    expect(envelopes[0]).toMatchObject({ type: "session.started", mode: "blueprint" });
    expect(envelopes[0]?.type === "session.started" && envelopes[0].notices).toHaveLength(1);

    const agents = envelopes.flatMap((e) => (e.type === "blueprint.agent" ? [e.agent] : []));
    const graphs = envelopes.flatMap((e) => (e.type === "blueprint.graph" ? [e.graph] : []));
    for (const blueprint of [...agents, ...graphs]) {
      expect(blueprint.source).toBe("code");
      expect(blueprint.location).not.toBeNull();
    }
    // More than one file, and a delegation from one file into another.
    const fileOf = new Map(agents.map((agent) => [agent.id, agent.location?.file]));
    expect(new Set([...fileOf.values(), ...graphs.map((g) => g.location?.file)]).size).toBe(4);
    expect(
      agents.some((agent) =>
        agent.delegates.some((d) => fileOf.get(d.agent_id) !== fileOf.get(agent.id)),
      ),
    ).toBe(true);

    // A model that is an expression, and a model class that still names its model.
    const models = agents.map((agent) => agent.model);
    expect(models.some((m) => m?.expression !== null && m?.name === m?.expression)).toBe(true);
    expect(models.some((m) => m?.expression !== null && m?.name !== m?.expression)).toBe(true);
    // An f-string template, a function, and an expression as instructions.
    const instructions = agents.flatMap((agent) => agent.instructions);
    expect(instructions.some((i) => i.dynamic && i.text !== null)).toBe(true);
    expect(instructions.some((i) => i.function !== null)).toBe(true);
    expect(instructions.some((i) => i.expression !== null)).toBe(true);
    // A computed setting, an opaque toolset, a type that could not be followed, and a tool
    // whose definition was not among the files.
    expect(agents.some((agent) => Object.keys(agent.settings.expressions).length > 0)).toBe(true);
    expect(agents.some((agent) => agent.toolsets.some((t) => t.opaque && t.expression))).toBe(true);
    expect(JSON.stringify(agents)).toContain('"x-python-type"');
    expect(agents.some((agent) => agent.tools.some((t) => t.parameters_schema === null))).toBe(
      true,
    );
    expect(agents.some((agent) => agent.name === null)).toBe(true);
  });

  test("index.json lists every recording", () => {
    const index = JSON.parse(fixtureText("index.json")) as RecordingIndex;
    expect(index.recordings.map((r) => r.src).sort()).toEqual([...recordings].sort());
    for (const entry of index.recordings) {
      expect(entry.title).not.toBe("");
      expect(entry.description).not.toBe("");
    }
  });
});
