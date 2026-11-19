from __future__ import annotations

import itertools
import json
import os
import threading
import types
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from pydantic_ai import Agent, InstrumentationSettings
from pydantic_ai.models.test import TestModel
from pydantic_graph import GraphBuilder, StepContext
from scenarios import PROMPT, Deps, build_team

import pydantic_ai_viz
from pydantic_ai_viz import Analysis, Session, _compat, analyze, record
from pydantic_ai_viz._models import parse_envelope
from pydantic_ai_viz._session import EventLog, active_session, blueprint_session


def validate(events: list[dict[str, Any]]) -> None:
    """Every envelope must satisfy the closed wire models, exactly as a recording line would."""
    for envelope in events:
        parse_envelope(json.dumps(envelope))


def span_event(kind: str, trace: int, span: int) -> dict[str, Any]:
    return {"type": f"span.{kind}", "span": {"trace_id": f"{trace:032x}", "span_id": f"{span:016x}"}}


def types_of(events: list[dict[str, Any]]) -> list[str]:
    return [envelope["type"] for envelope in events]


def agent_named(name: str | None = None) -> Agent[None, str]:
    return Agent(TestModel(), name=name)


# ---------------------------------------------------------------- event log


def test_seq_starts_at_zero_and_envelope_fields_lead() -> None:
    log = EventLog()
    first = log.append({"type": "session.started", "title": "t"})
    second = log.append({"type": "blueprint.agent", "agent": {}})
    assert first is not None
    assert second is not None
    assert list(first) == ["seq", "ts", "type", "title"]
    assert (first["seq"], second["seq"]) == (0, 1)
    assert second["ts"] >= first["ts"] > 1_600_000_000_000  # epoch milliseconds
    assert log.last_seq == 1
    assert log.snapshot() == [first, second]


def test_seq_order_is_list_order_under_concurrent_threads() -> None:
    log = EventLog()
    threads, per_thread = 8, 500
    start = threading.Barrier(threads)

    def write(worker: int) -> None:
        start.wait()
        for index in range(per_thread):
            log.append({"type": "x", "worker": worker, "index": index})

    workers = [threading.Thread(target=write, args=(worker,)) for worker in range(threads)]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()

    events = log.snapshot()
    assert [envelope["seq"] for envelope in events] == list(range(threads * per_thread))
    assert all(a["ts"] <= b["ts"] for a, b in itertools.pairwise(events))
    for worker in range(threads):  # nothing lost, and each writer's own order is kept
        assert [e["index"] for e in events if e["worker"] == worker] == list(range(per_thread))


def test_eviction_drops_oldest_completed_traces_whole() -> None:
    log = EventLog(max_events=8)
    log.append({"type": "session.started"})
    log.append({"type": "blueprint.agent", "agent": {"id": "a1"}})
    for trace in (1, 2, 3):
        log.append(span_event("started", trace, trace))
        log.append({"type": "stream.delta", "trace_id": f"{trace:032x}", "span_id": f"{trace:016x}"})
        log.append(span_event("ended", trace, trace))
    events = log.snapshot()
    # 11 events were appended; trace 1 went when the 9th arrived, nothing more had to go after that.
    assert [envelope["seq"] for envelope in events] == [0, 1, 5, 6, 7, 8, 9, 10]
    assert types_of(events)[:2] == ["session.started", "blueprint.agent"]
    assert {e["span"]["trace_id"] for e in events if "span" in e} == {f"{2:032x}", f"{3:032x}"}
    assert log.last_seq == 10


def test_eviction_never_drops_session_started_blueprints_or_a_running_trace() -> None:
    log = EventLog(max_events=4)
    log.append({"type": "session.started"})
    log.append(span_event("started", 1, 1))  # the long-running trace: never ends in this test
    for index in range(5):
        log.append({"type": "blueprint.agent", "agent": {"id": f"a{index}"}})
    for trace in (2, 3):
        log.append(span_event("started", trace, trace))
        log.append(span_event("ended", trace, trace))
    events = log.snapshot()
    # Over the limit, and rightly so: all that may go is trace 2, which went when trace 3 completed.
    assert types_of(events) == [
        "session.started",
        "span.started",
        *["blueprint.agent"] * 5,
        "span.started",
        "span.ended",
    ]
    assert [e["span"]["trace_id"] for e in events if "span" in e] == [f"{1:032x}", f"{3:032x}", f"{3:032x}"]
    assert log.last_seq == 10


def test_trace_with_an_open_child_is_not_complete() -> None:
    log = EventLog(max_events=3)
    log.append(span_event("started", 1, 1))
    log.append(span_event("started", 1, 2))
    log.append(span_event("ended", 1, 2))
    log.append({"type": "blueprint.agent", "agent": {}})  # over the limit, but span 1 is still open
    assert len(log.snapshot()) == 4
    log.append(span_event("ended", 1, 1))  # now complete, but the newest finished trace always stays
    assert len(log.snapshot()) == 5
    log.append(span_event("started", 2, 3))
    log.append(span_event("ended", 2, 3))  # a newer trace finished: trace 1 goes, whole
    assert types_of(log.snapshot()) == ["blueprint.agent", "span.started", "span.ended"]


def test_a_trace_larger_than_the_log_survives_its_own_completion() -> None:
    log = EventLog(max_events=4)
    log.append({"type": "session.started"})
    log.append(span_event("started", 1, 1))
    for _ in range(10):
        log.append({"type": "stream.delta", "trace_id": f"{1:032x}", "span_id": f"{1:016x}"})
    log.append(span_event("ended", 1, 1))
    assert len(log.snapshot()) == 13  # the run someone just watched is still there to look at and to save
    log.append(span_event("started", 2, 2))
    assert len(log.snapshot()) == 14
    log.append(span_event("ended", 2, 2))  # only now is there something newer to show
    assert types_of(log.snapshot()) == ["session.started", "span.started", "span.ended"]


def test_a_trace_that_finishes_while_a_larger_one_is_open_stays_until_a_newer_one_finishes() -> None:
    log = EventLog(max_events=4)
    log.append(span_event("started", 1, 1))  # the long-running trace
    for _ in range(6):
        log.append({"type": "stream.delta", "trace_id": f"{1:032x}", "span_id": f"{1:016x}"})

    def traces() -> list[int]:
        return sorted({int(e["span"]["trace_id"] if "span" in e else e["trace_id"], 16) for e in log.snapshot()})

    log.append(span_event("started", 2, 2))
    log.append(span_event("ended", 2, 2))
    assert traces() == [1, 2]
    log.append(span_event("started", 3, 3))
    assert traces() == [1, 2, 3]
    log.append(span_event("ended", 3, 3))
    assert traces() == [1, 3]
    log.append(span_event("ended", 1, 1))  # the oldest trace is now the one that finished last
    assert traces() == [1]


def test_max_events_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        EventLog(max_events=0)


def test_subscription_gets_backlog_then_tails() -> None:
    log = EventLog()
    for index in range(3):
        log.append({"type": "x", "index": index})
    with log.subscribe() as subscription:
        assert [e["seq"] for e in subscription.get(timeout=0)] == [0, 1, 2]
        assert subscription.get(timeout=0) == []
        log.append({"type": "x", "index": 3})
        assert [e["seq"] for e in subscription.get(timeout=0)] == [3]


def test_subscription_resumes_after_a_seq_and_restarts_when_ahead_of_the_log() -> None:
    log = EventLog()
    for index in range(4):
        log.append({"type": "x", "index": index})
    assert [e["seq"] for e in log.subscribe(after=1).get(timeout=0)] == [2, 3]
    assert log.subscribe(after=3).get(timeout=0) == []
    # A reader that is ahead of us last talked to another process: it gets everything.
    assert [e["seq"] for e in log.subscribe(after=99).get(timeout=0)] == [0, 1, 2, 3]


def test_subscription_skips_evicted_events() -> None:
    log = EventLog(max_events=2)
    subscription = log.subscribe()
    for trace in (1, 2, 3):
        log.append(span_event("started", trace, trace))
        log.append(span_event("ended", trace, trace))
    assert [e["seq"] for e in subscription.get(timeout=0)] == [4, 5]


def test_blocked_reader_wakes_on_append_and_on_close() -> None:
    log = EventLog()
    subscription = log.subscribe()
    got: list[list[dict[str, Any]]] = []
    reader = threading.Thread(target=lambda: got.extend([subscription.get(timeout=30), subscription.get(timeout=30)]))
    reader.start()
    log.append({"type": "x"})
    log.close()
    reader.join(timeout=10)
    assert not reader.is_alive()
    assert [len(batch) for batch in got] == [1, 0]
    assert subscription.closed
    assert log.append({"type": "late"}) is None  # a closed log is frozen
    assert len(log.snapshot()) == 1


def test_subscriber_count_and_wait_for_subscriber() -> None:
    log = EventLog()
    assert log.wait_for_subscriber(timeout=0) is False
    waiter_saw: list[bool] = []
    waiter = threading.Thread(target=lambda: waiter_saw.append(log.wait_for_subscriber(timeout=30)))
    waiter.start()
    subscription = log.subscribe()
    waiter.join(timeout=10)
    assert waiter_saw == [True]
    assert log.subscriber_count == 1
    subscription.close()
    subscription.close()  # idempotent
    assert log.subscriber_count == 0


# ------------------------------------------------------------------ session


def test_session_started_is_seq_zero() -> None:
    with record(title="My session", include_content=False) as session:
        (started,) = session.events
    assert {key: value for key, value in started.items() if key != "ts"} == {
        "seq": 0,
        "type": "session.started",
        "pviz": 1,
        "session_id": session.session_id,
        "title": "My session",
        "mode": "capture",
        "include_content": False,
        "stream": False,
        "notices": [],
        "versions": {
            "pydantic_ai_viz": pydantic_ai_viz.__version__,
            "pydantic_ai": _compat.pydantic_ai_version(),
            "python": started["versions"]["python"],
        },
    }
    assert len(session.session_id) == 32
    assert started["versions"]["python"].count(".") == 2
    assert session.url is None
    validate([started])


def test_a_blueprint_session_says_so_and_carries_its_notices() -> None:
    with Session(instrument=False, mode="blueprint", notices=["skipped broken.py: invalid syntax"]) as session:
        session.register(agent_named("solo"))
        events = session.events
    assert (session.mode, events[0]["mode"]) == ("blueprint", "blueprint")
    assert events[0]["notices"] == ["skipped broken.py: invalid syntax"]
    assert types_of(events) == ["session.started", "blueprint.agent"]
    validate(events)


def test_a_blueprint_session_cannot_instrument() -> None:
    with pytest.raises(ValueError, match="instrument=False"):
        Session(mode="blueprint")
    assert active_session() is None


def test_title_defaults_to_the_first_named_target() -> None:
    with record(agent_named(), agent_named("triage")) as session:
        assert session.title == "triage"
    with record() as session:
        assert session.title == "pydantic-ai-viz"


def test_register_emits_blueprints_for_the_delegation_closure_once() -> None:
    team = build_team()
    with record(team.main) as session:
        session.register(team.main, team.researcher)  # both known already: no new events
        events = session.events
    validate(events)
    assert types_of(events) == ["session.started", "blueprint.agent", "blueprint.agent"]
    main, researcher = (envelope["agent"] for envelope in events[1:])
    assert (main["id"], main["name"], researcher["id"], researcher["name"]) == ("a1", "main", "a2", "researcher")
    assert main["delegates"] == [{"tool_name": "delegate_research", "agent_id": "a2", "evidence": "object"}]
    assert main["source"] == "object"


def test_register_module_uses_variable_names_as_labels() -> None:
    module = types.ModuleType("my_agents")
    helper = agent_named()
    front = agent_named()

    @front.tool_plain
    async def ask_helper(question: str) -> str:  # pyright: ignore[reportUnusedFunction]
        return (await helper.run(question)).output

    builder = GraphBuilder(input_type=int, output_type=int)

    @builder.step
    async def double(ctx: StepContext[None, None, int]) -> int:
        return ctx.inputs * 2

    builder.add(builder.edge_from(builder.start_node).to(double), builder.edge_from(double).to(builder.end_node))
    module.front = front  # pyright: ignore[reportAttributeAccessIssue]
    module.flow = builder.build()  # pyright: ignore[reportAttributeAccessIssue]
    module.helper = helper  # pyright: ignore[reportAttributeAccessIssue]
    module.not_a_target = 3  # pyright: ignore[reportAttributeAccessIssue]

    with record(module) as session:
        events = session.events
    validate(events)
    assert session.title == "my_agents"
    assert types_of(events) == ["session.started", "blueprint.agent", "blueprint.agent", "blueprint.graph"]
    # `helper` is first reached as a delegate of `front`, and still gets its variable name.
    assert [(e["agent"]["id"], e["agent"]["name"], e["agent"]["label"]) for e in events[1:3]] == [
        ("a1", None, "front"),
        ("a2", None, "helper"),
    ]
    assert (events[3]["graph"]["id"], events[3]["graph"]["label"]) == ("g1", "flow")
    assert [node["id"] for node in events[3]["graph"]["nodes"]] == ["__start__", "double", "__end__"]


def test_unnamed_agent_without_a_variable_name_gets_a_numbered_label() -> None:
    with record(agent_named("first"), agent_named()) as session:
        assert [e["agent"]["label"] for e in session.events[1:]] == ["first", "agent-2"]


def test_a_name_belongs_to_one_blueprint_only() -> None:
    with record(agent_named("twin"), agent_named("twin")) as session:
        first, second = (envelope["agent"] for envelope in session.events[1:])
    assert (first["name"], first["warnings"]) == ("twin", [])
    assert (second["name"], second["label"]) == (None, "twin")
    assert second["warnings"] == [
        "another agent named 'twin' is already registered; runs named 'twin' are shown on that one"
    ]


TEAM_SOURCE = """
from pydantic_ai import Agent, RunContext
from pydantic_graph import GraphBuilder

helper = Agent("test")
lead = Agent("test", name="lead", instructions="Lead the work.")
Agent("test")


@lead.tool
async def ask_helper(ctx: RunContext[None], question: str) -> str:
    return (await helper.run(question)).output


flow = GraphBuilder(name="flow")
GraphBuilder()
"""


def read_team(directory: Path) -> Analysis:
    path = directory / "team.py"
    path.write_text(TEAM_SOURCE, encoding="utf-8")
    return analyze(path)


def test_register_an_analysis_adds_what_was_read_from_source_under_ids_of_the_session(tmp_path: Path) -> None:
    found = read_team(tmp_path)
    assert [b.id for b in found.blueprints] == ["a1", "a2", "a3", "g1", "g2"]
    builder = GraphBuilder(name="live-graph", output_type=int)
    with record(agent_named("live"), builder.build(validate_graph_structure=False), instrument=False) as session:
        session.register(found)
        session.register(found)  # the same analysis again: nothing new
        events = session.events
    validate(events)
    agents = [envelope["agent"] for envelope in events if envelope["type"] == "blueprint.agent"]
    assert [(a["id"], a["label"], a["name"], a["source"]) for a in agents] == [
        ("a1", "live", "live", "object"),
        ("a2", "helper", None, "code"),
        ("a3", "lead", "lead", "code"),
        ("a4", "agent-4", None, "code"),  # numbered labels follow the session's ids
    ]
    assert agents[2]["delegates"] == [{"tool_name": "ask_helper", "agent_id": "a2", "evidence": "code"}]
    assert agents[2]["location"] == {"file": "team.py", "line": 6}
    graphs = [envelope["graph"] for envelope in events if envelope["type"] == "blueprint.graph"]
    assert [(g["id"], g["label"], g["source"]) for g in graphs] == [
        ("g1", "live-graph", "object"),
        ("g2", "flow", "code"),
        ("g3", "graph-3", "code"),
    ]


def test_a_name_read_from_source_that_is_taken_already_is_given_up_with_a_warning(tmp_path: Path) -> None:
    with record(agent_named("lead"), read_team(tmp_path), instrument=False) as session:
        agents = [envelope["agent"] for envelope in session.events if envelope["type"] == "blueprint.agent"]
    assert [(a["id"], a["name"], a["label"]) for a in agents] == [
        ("a1", "lead", "lead"),
        ("a2", None, "helper"),
        ("a3", None, "lead"),
        ("a4", None, "agent-4"),
    ]
    assert agents[2]["warnings"] == [
        "another agent named 'lead' is already registered; runs named 'lead' are shown on that one"
    ]


def test_a_blueprint_session_keeps_names_that_two_agents_share(tmp_path: Path) -> None:
    # No run is ever matched to a blueprint in it, so nothing needs a name to itself.
    for name in ("first.py", "second.py"):
        source = 'from pydantic_ai import Agent\nwriter = Agent("test", name="writer")\n'
        (tmp_path / name).write_text(source, encoding="utf-8")
    with blueprint_session(analyze(tmp_path)) as session:
        agents = [envelope["agent"] for envelope in session.events if envelope["type"] == "blueprint.agent"]
    assert [(a["name"], a["label"], a["warnings"]) for a in agents] == [("writer", "writer", [])] * 2


def test_a_blueprint_session_of_the_demos_has_no_warnings() -> None:
    demos = Path(pydantic_ai_viz.__file__).parent / "demos"
    with blueprint_session(analyze(demos)) as session:
        agents = [envelope["agent"] for envelope in session.events if envelope["type"] == "blueprint.agent"]
    assert agents
    assert [a["warnings"] for a in agents] == [[]] * len(agents)


def test_a_blueprint_session_of_an_analysis_is_titled_after_its_file_and_carries_its_notices(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def (:\n", encoding="utf-8")
    read_team(tmp_path)
    found = analyze(tmp_path / "team.py", tmp_path / "broken.py")
    with blueprint_session(found) as session:
        events = session.events
    validate(events)
    assert (events[0]["title"], events[0]["mode"]) == ("team", "blueprint")
    assert len(events[0]["notices"]) == 1
    assert events[0]["notices"][0].startswith("skipped broken.py: it is not valid Python")
    assert types_of(events) == ["session.started", *["blueprint.agent"] * 3, *["blueprint.graph"] * 2]
    assert active_session() is None  # nothing was instrumented
    with blueprint_session(found, title="Mine") as session:
        assert session.title == "Mine"
    with pytest.raises(TypeError, match="got int"):
        blueprint_session(3)  # pyright: ignore[reportArgumentType]


@pytest.mark.anyio
async def test_runs_are_drawn_on_the_blueprints_read_from_source(tmp_path: Path) -> None:
    lead = Agent(TestModel(), name="lead")
    helper = Agent(TestModel())  # no name: Pydantic AI infers "helper" from this variable when it runs
    with record(read_team(tmp_path)) as session:
        await lead.run("Go")
        await helper.run("Go")
        events = session.events
    validate(events)
    latest = {e["agent"]["id"]: e["agent"] for e in events if e["type"] == "blueprint.agent"}
    # No extra blueprint was made up for the runs: they belong to the ones read from source.
    assert {key: (a["name"], a["label"], a["source"]) for key, a in latest.items()} == {
        "a1": ("helper", "helper", "code"),
        "a2": ("lead", "lead", "code"),
        "a3": (None, "agent-3", "code"),
    }
    runs = [e["span"] for e in events if e["type"] == "span.started" and e["span"]["kind"] == "agent_run"]
    assert [run["agent_name"] for run in runs] == ["lead", "helper"]
    # What the source says stays; the tool read from source is still there after the run.
    assert [(t["name"], t["origin"]) for t in latest["a2"]["tools"]] == [("ask_helper", "code")]
    assert latest["a2"]["location"] == {"file": "team.py", "line": 6}


def test_register_rejects_other_objects() -> None:
    with record() as session, pytest.raises(TypeError, match="got str"):
        session.register("pkg.module:agent")  # pyright: ignore[reportArgumentType]
    with pytest.raises(TypeError, match="got int"):
        record(3)  # pyright: ignore[reportArgumentType]
    assert active_session() is None  # the failed record() closed its session


@pytest.mark.anyio
async def test_run_is_captured_in_order_and_every_line_validates() -> None:
    team = build_team()
    with record(team.main) as session:
        await team.main.run(PROMPT, deps=Deps())
        events = session.events
    validate(events)
    assert [envelope["seq"] for envelope in events] == list(range(len(events)))
    assert all(a["ts"] <= b["ts"] for a, b in itertools.pairwise(events))

    started = {e["span"]["span_id"]: e["seq"] for e in events if e["type"] == "span.started"}
    ended = {e["span"]["span_id"]: e["seq"] for e in events if e["type"] == "span.ended"}
    parents = {e["span"]["span_id"]: e["span"]["parent_id"] for e in events if e["type"] == "span.started"}
    assert len(started) == len(ended) == 13
    for span_id, seq in started.items():
        assert seq < ended[span_id]  # start before end
        parent = parents[span_id]
        if parent is not None:
            assert started[parent] < seq  # a parent starts before its children...
            assert ended[span_id] < ended[parent]  # ...and ends after them
    assert len({e["span"]["trace_id"] for e in events if "span" in e}) == 1
    for envelope in events:
        if envelope["type"] == "span.started":
            assert envelope["span"]["start_ms"] <= envelope["ts"]  # same clock as the envelope


def test_seq_is_gapless_when_agents_run_in_concurrent_threads() -> None:
    agents = [agent_named(f"worker-{index}") for index in range(6)]
    failures: list[BaseException] = []
    start = threading.Barrier(len(agents))

    def work(agent: Agent[None, str]) -> None:
        try:
            start.wait()
            for _ in range(5):
                agent.run_sync("hi")
        except BaseException as exc:
            failures.append(exc)

    with record(*agents) as session:
        threads = [threading.Thread(target=work, args=(agent,)) for agent in agents]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        events = session.events
    assert failures == []
    validate(events)
    assert [envelope["seq"] for envelope in events] == list(range(len(events)))
    spans = [e for e in events if e["type"].startswith("span.")]
    assert len(spans) == 6 * 5 * 2 * 2  # per run: one agent_run and one model_request, each started and ended
    position = {(e["type"], e["span"]["span_id"]): e["seq"] for e in spans}
    for (kind, span_id), seq in position.items():
        if kind == "span.ended":
            assert position["span.started", span_id] < seq


@pytest.mark.anyio
async def test_eviction_keeps_whole_traces_session_started_and_blueprints() -> None:
    agent = agent_named("looper")
    with record(agent, max_events=12) as session:
        for _ in range(6):
            await agent.run("hi")  # 4 events per run
        events = session.events
    validate(events)
    assert types_of(events)[:2] == ["session.started", "blueprint.agent"]
    assert events[0]["seq"] == 0
    assert len(events) <= 12
    assert events[2]["seq"] > 2  # a gap: the oldest runs are gone
    by_trace: dict[str, list[str]] = {}
    for envelope in events[2:]:
        by_trace.setdefault(envelope["span"]["trace_id"], []).append(f"{envelope['type']} {envelope['span']['kind']}")
    assert len(by_trace) == 2
    for kinds in by_trace.values():  # the survivors are complete
        assert kinds == [
            "span.started agent_run",
            "span.started model_request",
            "span.ended model_request",
            "span.ended agent_run",
        ]


# ---------------------------------------------------------------- lifecycle


def test_only_one_instrumenting_session_at_a_time() -> None:
    with record() as first:
        assert active_session() is first
        with pytest.raises(RuntimeError, match="a pydantic-ai-viz session is already active; close it first"):
            record()
        assert active_session() is first  # the failed attempt left nothing behind
        with record(instrument=False) as passive:  # non-instrumenting sessions may coexist
            assert active_session() is passive
        assert active_session() is first
    assert active_session() is None
    with record() as again:  # and after closing, a new one is fine
        assert not again.closed


def test_close_is_idempotent_and_freezes_the_log() -> None:
    agent = agent_named("late")
    session = record(agent)
    session.close()
    session.close()
    assert session.closed
    agent.run_sync("hi")  # not instrumented any more
    session.register(agent_named("later"))  # accepted, but nothing is recorded
    assert types_of(session.events) == ["session.started", "blueprint.agent"]


def test_close_restores_the_previous_instrumentation() -> None:
    assert _compat.instrumentation_default() is False
    mine = InstrumentationSettings(tracer_provider=TracerProvider())
    Agent.instrument_all(mine)
    with record():
        assert _compat.instrumentation_default() is not mine
    assert _compat.instrumentation_default() is mine


def test_close_leaves_instrumentation_someone_else_set_meanwhile() -> None:
    theirs = InstrumentationSettings(tracer_provider=TracerProvider())
    with record():
        Agent.instrument_all(theirs)
    assert _compat.instrumentation_default() is theirs


def test_instrument_false_touches_nothing_global_and_captures_through_span_processor() -> None:
    agent = agent_named("mine")
    with record(agent, instrument=False) as session:
        assert _compat.instrumentation_default() is False
        agent.run_sync("unseen")
        assert types_of(session.events) == ["session.started", "blueprint.agent"]

        provider = TracerProvider()
        provider.add_span_processor(session.span_processor)
        agent.instrument = InstrumentationSettings(tracer_provider=provider)
        agent.run_sync("seen")
        events = session.events
    validate(events)
    assert types_of(events)[2:] == ["span.started", "span.started", "span.ended", "span.ended"]


def test_session_can_be_constructed_directly() -> None:
    with Session(title="direct") as session:
        assert active_session() is session
        assert session.title == "direct"
    assert active_session() is None


def test_public_api_exports() -> None:
    assert {"Session", "record", "blueprint"} <= set(pydantic_ai_viz.__all__)
    assert pydantic_ai_viz.record is record


def test_close_turns_instrumentation_off_when_the_previous_setting_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_compat, "instrumentation_default", lambda: None)
    with record():
        assert isinstance(Agent._instrument_default, InstrumentationSettings)  # pyright: ignore[reportPrivateUsage]
    assert Agent._instrument_default is False  # pyright: ignore[reportPrivateUsage]


def test_an_instrumenting_session_quiets_the_pydantic_ai_banner_unless_the_user_chose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The banner would say "observability: off": the session's tracer provider is private.
    monkeypatch.delenv("PYDANTIC_AI_NO_BANNER", raising=False)
    with record(instrument=False):
        assert "PYDANTIC_AI_NO_BANNER" not in os.environ
    with record():
        assert os.environ["PYDANTIC_AI_NO_BANNER"] == "1"
    monkeypatch.setenv("PYDANTIC_AI_NO_BANNER", "0")
    with record():
        assert os.environ["PYDANTIC_AI_NO_BANNER"] == "0"
