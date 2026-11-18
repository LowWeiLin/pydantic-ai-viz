# How static analysis works

`pydantic-ai-viz view agents.py` and [`pviz.analyze("agents.py")`](../reference/python-api.md#analyze) draw your agents **from their source code**. The file is read as text and parsed. It is never imported and never executed.

That has three consequences worth knowing before you rely on it:

- **Nothing in the file runs.** Not the top-level code, not a decorator, not a default argument. Pointing `view` at code you do not trust is safe in the way opening it in an editor is safe.
- **Nothing the file imports has to be installed.** A file that starts with `from dotenv import load_dotenv` is read the same whether or not `python-dotenv` is there. Pydantic AI providers, API keys and environment variables do not matter either.
- **Only what the source spells out is known.** `Agent("openai:gpt-5.2")` names its model. `Agent(get_model())` does not, and the viewer shows the expression `get_model()` instead of pretending to know what it returns.

The third point is the rule the whole analyser follows: **a value is reported when the source states it, and the source text is reported when the source computes it. Nothing is guessed.**

## What happens to a file

1. **Read.** The bytes are decoded the way Python decodes source (UTF-8, or the encoding the file declares). A file that is too large, unreadable or not valid Python is skipped, and the reason becomes a notice in the viewer.
2. **Parse.** The text goes through Python's own parser, the standard-library [`ast`](https://docs.python.org/3/library/ast.html) module, which produces a syntax tree. Parsing is not running: no statement of the file is evaluated.
3. **Index.** The tree is walked once to record which names each scope binds: assignments, `def`, `class`, imports. A name that only running the code could give a value, such as a loop variable or a function parameter, is recorded as unknown.
4. **Find.** Every call of Pydantic AI's `Agent`, and of pydantic-graph's `GraphBuilder` or `Graph(nodes=[...])`, is a blueprint. A call counts only when the name it calls can be traced to an import from `pydantic_ai` or `pydantic_graph`: a class of your own that happens to be called `Agent` is not an agent.
5. **Attach.** Decorators and registration calls (`@agent.tool`, `@agent.instructions`, `toolset.add_function(f)`, `builder.add(...)`) are traced back to the construction they belong to, following aliases, factory functions that return the agent, and `self.agent` attributes.
6. **Describe.** Each argument is read as a value if it is a literal, and reported as source text otherwise.

## What is read

| From the source | In the blueprint |
|---|---|
| `Agent("openai:gpt-5.2", ...)` | The model, split into provider and name |
| `Agent(get_model(), ...)` | The expression `get_model()`, marked as computed, and a warning |
| `name="triage"`, or the variable the agent is assigned to | The agent's name, or its label |
| `instructions="..."`, also concatenated or wrapped in `textwrap.dedent` | The text |
| `instructions=f"Today is {today}"` | The template, with `{today}` left in place, marked dynamic |
| `@agent.instructions` / `@agent.system_prompt`, or a function passed as an instruction | The function's name, marked dynamic |
| `@agent.tool`, `@agent.tool_plain`, `tools=[f, Tool(g, name=...)]` | One tool each, with the registration's `name`, `description`, `retries` and `requires_approval` |
| A tool function's signature | The parameter JSON Schema: types, defaults, required parameters. The leading `RunContext` parameter is left out |
| A tool function's docstring | The description, and each parameter's description (Google, NumPy and Sphinx styles, read with `griffe` as Pydantic AI does) |
| `output_type=Invoice` | The type's name and a JSON Schema built from the class definition: pydantic models, dataclasses, `TypedDict`, `NamedTuple`, enums, unions, `Literal`, `Annotated[..., Field(...)]` |
| `deps_type`, `retries`, `end_strategy`, `tool_timeout`, `model_settings` | The values, where they are literals |
| `toolsets=[FunctionToolset(...), MCPServerStdio(...)]` | A function toolset with its tools; anything else as an opaque toolset named after its class |
| `capabilities=[...]` | The class name of each capability |
| A tool whose body calls `other_agent.run(...)` (or `run_sync`, `run_stream`, `iter`) | A delegation edge to that agent |
| `GraphBuilder` with `@builder.step`, `builder.add(...)`, `add_edge`, decisions, joins and maps | The graph's nodes and edges, numbered the way pydantic-graph numbers them |
| `BaseNode` classes | Edges from what each `run` method declares it returns |

Everything found carries the file and line it was found at, so the viewer can say "defined at `agents.py:42`".

## What cannot be read, and how you can tell

When a value depends on running the code, the blueprint says so in three ways:

- the field holds the **source text** of the expression instead of a value;
- the blueprint's **warnings** list names it in plain words, for example "the model is set by `get_model()`, so it is only known when the code runs";
- the command line prints how many warnings there were.

Typical cases:

- **Computed arguments**: `Agent(settings.model)`, `instructions=load_prompt("triage")`, `tools=build_tools()`.
- **Names from other files.** At present each file is read by itself together with the other files you name; a class or function imported from a file that is not analysed is known by name only, so an output type defined elsewhere has no schema.
- **Defaults of Pydantic AI.** A setting the source does not mention stays empty. The analyser does not fill in what the installed Pydantic AI would use, because the code may be written for another version.
- **Tools of MCP servers and dynamic toolsets.** They exist only once the server is running.
- **Graph edges added in a loop or by a helper function.** The steps are listed, the edges that are written out are drawn, and a warning names the call that could not be followed.

## When you want the real objects instead

```bash
pydantic-ai-viz view --import my_app.agents:support_agent
```

`--import` loads the target and reads the live `Agent` and `Graph` objects, as [`pviz.blueprint(obj)`](../reference/python-api.md#blueprint) does. The result is complete: Pydantic AI's defaults, computed values and schemas exactly as the model would be sent them.

!!! warning "`--import` executes the target"

    Importing a module runs its top-level code and the top-level code of everything it imports. Use `--import` only on code you trust, in an environment where its dependencies are installed.

| | Default (`view FILE`) | `view --import` |
|---|---|---|
| Runs the target's code | Never | Yes, on import |
| Needs the target's dependencies | No | Yes |
| Computed values (`get_model()`) | Shown as the expression | Resolved |
| Pydantic AI defaults | Not shown | Shown |
| Source locations | Yes | No |
| A blueprint's `source` | `"code"` | `"object"` |

Whichever way a blueprint was made, runs are drawn on it in the same way; see [Blueprint vs run](blueprint-vs-run.md).

## Limits that keep it safe on unknown code

The analyser is written to be pointed at code nobody has reviewed:

- It contains no `eval`, no `exec` and no import of the code it reads. Literal values are read by a small reader of its own that counts what it visits, not by `ast.literal_eval`.
- Every walk over the source is bounded: following names, opening up nested types, expanding `*lists`. Cyclic aliases and self-referring models end in a warning.
- A file larger than 2 MB is skipped, and an analysis stops after 30 seconds. Both limits can be changed through [`Limits`](../reference/python-api.md#analyze) and both leave a notice.
- A file that is nested too deeply for Python's parser is skipped, never a crash.
