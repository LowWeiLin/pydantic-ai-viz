# Visualize an agent

A **blueprint** is a picture of what an agent *is*: its model, instructions, tools, output type and the agents it hands work to. `pydantic-ai-viz view` reads it from your source code, which is never executed; from Python it can also be read from the `Agent` object. Either way no agent is run, no model is called, and no API key is needed, so you can draw an agent that is configured for a provider you have no credentials for.

<figure class="screenshot" markdown="span">
  ![pydantic-ai-viz view on the demos' own source: their files in the sidebar, the triage agent with its tools, output and a dashed delegation edge to the refunds agent, and the inspector on a tool with its parameters and the line it is defined at](../assets/screenshots/view-dark.png)
  <figcaption>What <code>pydantic-ai-viz view src/pydantic_ai_viz/demos</code> shows</figcaption>
</figure>

## From the command line

```bash
pydantic-ai-viz view TARGET...
```

| Target | What is drawn |
|---|---|
| `my_app/` | Every agent and graph in every `.py` file below that directory |
| `my_app/agents.py` | Every agent and graph in that file, including those made inside functions and classes |
| `my_app/agents.py:support_agent` | The agent assigned to `support_agent`, and the agents it delegates to |
| `my_app.agents`, `my_app.agents:support_agent` | The same, with the file found from the module name; nothing is imported to find it |
| `gh:owner/repo[@REF][/SUBDIR]`, or a `https://github.com/...` URL | A GitHub repository, fetched with `git` and read like a directory |

You can pass several targets. Imports between your own files are followed, so an agent in `agents.py` is drawn with the tools, output model and toolsets it imports from `tools.py` and `models.py`, and with the agents it delegates to wherever they live. The [CLI reference](../reference/cli.md#targets) has every form.

`view` **reads the source and never runs it**: nothing is imported, so it does not matter whether the dependencies are installed, and a script that calls its agent at the bottom is safe to view. What the code computes when it runs, such as `Agent(get_model())`, is shown as that expression, marked "decided at run time", with a warning on the agent card. A model read from an environment variable, as in `Agent(os.getenv("MODEL", "openai:gpt-5.2"))` or through a helper of your project, is shown as `env MODEL`, with the model class when the code builds one and the source's default labelled as a default, never as the model. [How view works](../concepts/how-view-works.md) explains what can be read and what cannot.

`view` serves the viewer and opens your browser, and prints what it read:

```text
Read 11 files without running any code: 5 agents, 0 graphs; 5 warnings about what the source does not spell out (see the viewer)
```

To write a file instead:

```bash
pydantic-ai-viz view my_app/ --export blueprint.html
```

That produces a single HTML file containing only blueprints, which is safe to attach to a design document or a pull request: it holds no run data. All flags are in the [CLI reference](../reference/cli.md#view).

When the source does not say enough, `pydantic-ai-viz view --import my_app.agents:support_agent` imports the module and reads the live objects instead. That executes the module's code, so use it only on code you trust; see [When to use `--import`](../concepts/how-view-works.md#when-to-use-import).

## From Python

[`analyze`](../reference/python-api.md#analyze) reads source the way `view` does and returns the blueprints as data:

```python
import pydantic_ai_viz as pviz

found = pviz.analyze("my_app/")
for agent in found.agents:
    print(agent.label, agent.location, [tool.name for tool in agent.tools])
```

[`blueprint`](../reference/python-api.md#blueprint) returns the same structure from a live object, which you have imported yourself. It starts no server and no session:

```python
from my_app.agents import support_agent

bp = pviz.blueprint(support_agent)

print(bp.model.name if bp.model else "no model")
for tool in bp.tools:
    print(tool.name, "-", tool.description)
print(bp.warnings)  # anything that could not be read
```

The result is an [`AgentBlueprint`](../reference/python-api.md#agentblueprint) (or a [`GraphBlueprint`](../reference/python-api.md#graphblueprint) for a graph). Both are Pydantic models, so `bp.model_dump()` and `bp.model_dump_json()` work, which is handy for a snapshot test that fails when someone changes an agent's tools or output schema by accident. The fields are documented in the [wire format](../reference/wire-format.md#blueprints).

To open the picture from Python, use [`export_html`](../reference/python-api.md#export_html) with the agent itself, or [`serve`](../reference/python-api.md#serve):

```python
pviz.export_html(support_agent, "blueprint.html")
```

## Reading the canvas

<figure class="screenshot" markdown="span">
  ![The blueprint of the triage agent: agent card, tools grouped by toolset, output node and a delegation edge to the refunds agent](../assets/screenshots/blueprint.png)
</figure>

Agent card

:   The agent's label and description, a chip with the model name (or `env NAME` for a model name read from an environment variable; hover it for the class and the default), the number of instructions, and a warnings badge if something could not be read. Models from the `test` and `function` providers get an **offline** chip, so a diagram of a test setup is never mistaken for production.

    The label is the agent's `name`. An agent created without `name=` has none until its first run (Pydantic AI then infers it from the variable name), so the label falls back to the variable name when the agent was found in a module or file, and to `agent-1`, `agent-2`, ... otherwise.

Tool nodes

:   One node per function tool, grouped under a header for the toolset it belongs to: "Agent tools" for `@agent.tool` and `@agent.tool_plain`, and the toolset's own label for each `FunctionToolset`. Select a tool to see its description, parameter schema, return schema, whether it takes the run context, and its retry limit. Select a toolset's header to list its tools, each with its parameter count and the first line of its description; picking one there is the same as clicking it on the canvas.

Opaque toolsets

:   A toolset whose tools cannot be listed without running it (an MCP server, or a toolset built by a function at run time) is shown as one placeholder node marked "decided at run time". Its tools are added to the blueprint, marked as observed, as runs offer them to the model: the first run adds the ones it was given, and a later run adds any it was not.

Output node

:   The output mode (`text`, `tool`, `native`, `prompted` or `auto`, or `unknown` when it could not be read) and the output type name. Select it for the JSON schema as a field table. Output tools such as `final_result` are part of this node, not tool nodes.

Delegation edges

:   An edge from a tool to another agent's card means "this tool runs that agent". The other agent is drawn in its own row underneath, one step to the right, with its own tools and output.

    - A **dashed** edge, labelled "in code", was found by reading the tool function's code. It is a good guess.
    - A **solid** edge, labelled "in a run", was seen happening in a run. It is a fact.

    See [Blueprint vs run](../concepts/blueprint-vs-run.md#evidence-on-delegation-edges) for how each is found.

Capabilities

:   An agent's capabilities are drawn in a box under its card, one row per capability, in the order of the [Pydantic AI capabilities docs](https://pydantic.dev/docs/ai/capabilities/overview/), with a short category label, an "on demand" mark for one the model loads when needed and a marker for one that gives the model a provider-side tool. A dashed line runs from a capability to each toolset it gives that is drawn. Select a row to open the capability in the inspector. Under a `Skills` capability, one indented row per skill (up to eight; the capability's row says "N skills") shows its name and a faint "hidden" tag for a skill the model may not load. In a run, a skill row shows whether it has been loaded.

    What Pydantic AI adds on its own is collapsed; see [Pydantic AI defaults](#pydantic-ai-defaults) below.

    ![The briefing agent of the capabilities demo: its capabilities in a box under the agent card, dashed lines to the toolsets they give, and the inspector on the house-style skill](../assets/screenshots/capabilities-dark.png#only-dark)
    ![The briefing agent of the capabilities demo: its capabilities in a box under the agent card, dashed lines to the toolsets they give, and the inspector on the house-style skill](../assets/screenshots/capabilities-light.png#only-light)

The inspector on the right shows the details for whatever is selected, in sections one under the other. For an agent: warnings, model, instructions, toolsets, output, delegates, settings and capabilities. Capabilities are grouped as in the [Pydantic AI capabilities docs](https://pydantic.dev/docs/ai/capabilities/overview/): harnesses, execution environments, tools, integrations, web and research, reasoning and delegation, context management, knowledge and memory, control and safety, self-extension, execution runtime and loop customization, with anything else under "Custom". Each capability is marked Core or Harness by the package it comes from (a harness `Coder` or `Researcher` on a live agent is listed as itself), and "on demand" when the model loads it when needed. It shows what it was given, the toolsets it gives the agent (each can be picked, and a capability can be picked from the agent's panel and from a toolset's) and its provider-side tools. A toolset's panel says which capability gave it. Capabilities that Pydantic AI adds to every agent stay folded under a "From Pydantic AI" disclosure, unless the agent configures one (`ToolSearch(max_results=3)`), which is then listed in its group. `CombinedCapability([...])` is shown as the capabilities in it. A blueprint read from source also says where each part is defined, as `file.py:line`, with a button that copies it.

Skills

:   Pick a skill row, or a skill in the panel of its `Skills` capability or of the agent, for its panel: when to use it, its instructions (the Markdown of its `SKILL.md`, which the model gets when it loads the skill, with a copy button), its path and the id `load_capability` takes. The skill is read from the live `Skills` capability or, from source, from the `SKILL.md` files in the folder read. A skill's text is part of the blueprint, so it is shown even when content capture is off. In a run, a `load_capability` call outlines the skill it loaded, its panel names it, and the `load_capability` tool's own panel lists what it can load.

Hooks

:   The functions a `Hooks` capability registers are rows under its row: the hook point, such as `before_tool_execute`, and the function. A hook limited to some tools says which. Pick one for the capability's panel, which lists them all in the order they run.

Approvals and sub-agents

:   A tool given `requires_approval=True` carries an "approval" tag with a lock, and one whose result the caller supplies says "runs outside"; in a run only their icons stay, to leave the name room next to the call count. A harness `SubAgents` capability draws a delegation edge from the agent's card to each sub-agent it lists, labelled with its tool, `delegate_task` by default. An agent wrapped in `TemporalAgent`, `DBOSAgent` or `PrefectAgent` has a chip that names the runtime.

    ![The oncall agent of the operations demo: its restart_service tool tagged needs approval, the Hooks capability with its two hook rows, and a delegation edge to the log_reader agent](../assets/screenshots/operations-dark.png#only-dark)
    ![The oncall agent of the operations demo: its restart_service tool tagged needs approval, the Hooks capability with its two hook rows, and a delegation edge to the log_reader agent](../assets/screenshots/operations-light.png#only-light)

### Pydantic AI defaults { #pydantic-ai-defaults }

The agent view and the run view show what you wrote. What Pydantic AI or the harness adds on its own is collapsed into counts. The "Pydantic AI defaults" button in the bar above the canvas, with a count such as "+12", shows it again; it appears only when the agent has any. It is a toggle (`aria-pressed`), hidden by default, and the choice is remembered in this browser, not in the page's address.

A default is:

- a capability Pydantic AI adds to every agent and you did not configure (`ToolSearch(max_results=3)` is yours), or one a `Coder` or `Researcher` added;
- a tool or toolset the library defines, such as the tools of `Memory` and `Planning` or `load_capability`, when the capability that gives it is yours and the toolset holds no function of yours (`WebSearch(local=search_archive)` is shown);
- a setting whose value is what a plain `Agent()` has (`end_strategy`, `tool_retries`, `output_retries`, `tool_timeout`), a blueprint from a live agent only.

When hidden, a capability row that gives hidden tools says "4 tools" (a stack such as `Coder` says "6 inside"), and in a run it shows the calls and state of those tools, so a call to a hidden `Memory` tool lights the `Memory` row. When shown, default parts have a dashed border and the word "default", and a stack's members say "via Coder". In the inspector, library toolsets sit under "From Pydantic AI" and default settings under "Pydantic AI defaults". The Mermaid export always lists every tool.

On a blueprint, what you pick is kept in the page's address, so a link or a reload opens on the same agent, tool, toolset or graph node.

## What a blueprint cannot show

A blueprint is drawn without running an agent, so some things are out of reach by design. (A blueprint read from source has a few more gaps, each marked with a warning: see [what cannot be read](../concepts/how-view-works.md#what-shows-as-decided-at-run-time-and-why).)

- **The text of dynamic instructions.** A function decorated with `@agent.instructions` or `@agent.system_prompt` is listed by name, marked dynamic. Its output depends on the run; you see the resolved text on each run's model requests. An f-string or `.format` template is shown whole, with the parts filled in at run time marked.
- **Tools of MCP and dynamic toolsets,** as described above.
- **Delegation that is not visible in the tool's code.** A blueprint read from source finds `await refunds_agent.run(...)` in a tool when `refunds_agent` can be traced to an agent: a variable of the module or of an enclosing scope, one imported from another file, an item of a dict or list written out in the source, or `ctx.deps.refunds` when the source shows the agent being passed as that dependency to `run`. It also finds an agent the tool hands to a function of your code that runs what it is given; an agent passed to anything else, such as a logging call, is not taken for a delegate. A blueprint read from a live object finds only module-level and enclosing-scope variables. Neither follows a helper function that picks and runs an agent of its own. Edges that were missed appear, solid, the first time the delegation runs.

When a blueprint is read from a live object, a handful of fields (instructions, system prompts, retry limits, output mode, native tools) have no public accessor in Pydantic AI and are read from private attributes. If a Pydantic AI release changes one, that field is left empty and a warning appears on the agent card. Nothing else is affected. The [compatibility page](../reference/compatibility.md#fields-read-from-private-attributes) lists them.

## Next

Blueprints become much more useful with runs on top: [Watch live runs](live-runs.md).
