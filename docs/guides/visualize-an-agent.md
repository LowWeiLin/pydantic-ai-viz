# Visualize an agent

A **blueprint** is a picture of what an agent *is*: its model, instructions, tools, output type and the agents it hands work to. It is read from the `Agent` object. Nothing is run, no model is called, and no API key is needed, so you can draw an agent that is configured for a provider you have no credentials for.

<figure class="screenshot" markdown="span">
  ![The blueprint of the triage agent: agent card, tools grouped by toolset, output node and a delegation edge to the refunds agent](../assets/screenshots/blueprint.png)
</figure>

## From the command line

```bash
pydantic-ai-viz view TARGET...
```

A target names an agent, a graph, or a place to look for them:

| Target | What is drawn |
|---|---|
| `my_app.agents:support_agent` | The object `support_agent` in the module `my_app.agents` |
| `my_app.agents` | Every agent and graph defined at the top level of that module |
| `path/to/file.py` | Every agent and graph defined at the top level of that file |
| `path/to/file.py:support_agent` | That one object from the file |

You can pass several targets. Module targets are imported, so run the command from the environment and directory where `import my_app.agents` works. File targets are loaded in a way that skips their `if __name__ == "__main__":` block, so a script that runs its agent at the bottom is safe to view.

`view` serves the viewer and opens your browser. To write a file instead:

```bash
pydantic-ai-viz view my_app.agents --export blueprint.html
```

That produces a single HTML file containing only blueprints, which is safe to attach to a design document: it holds no run data. All flags are in the [CLI reference](../reference/cli.md#view).

## From Python

[`blueprint`](../reference/python-api.md#blueprint) returns the same structure as data. It starts no server and no session:

```python
import pydantic_ai_viz as pviz
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

Agent card

:   The agent's label and description, a chip with the model name, the number of instructions, and a warnings badge if something could not be read. Models from the `test` and `function` providers get an **offline** chip, so a diagram of a test setup is never mistaken for production.

    The label is the agent's `name`. An agent created without `name=` has none until its first run (Pydantic AI then infers it from the variable name), so the label falls back to the variable name when the agent was found in a module or file, and to `agent-1`, `agent-2`, ... otherwise.

Tool nodes

:   One node per function tool, grouped under a header for the toolset it belongs to: "Agent tools" for `@agent.tool` and `@agent.tool_plain`, and the toolset's own label for each `FunctionToolset`. Select a tool to see its description, parameter schema, return schema, whether it takes the run context, and its retry limit.

Opaque toolsets

:   A toolset whose tools cannot be listed without running it (an MCP server, or a toolset built by a function at run time) is shown as one placeholder node marked "resolved at run time". Its tools are added to the blueprint, marked as observed, as runs offer them to the model: the first run adds the ones it was given, and a later run adds any it was not.

Output node

:   The output mode (`text`, `tool`, `native`, `prompted` or `auto`) and the output type name. Select it for the JSON schema as a field table. Output tools such as `final_result` are part of this node, not tool nodes.

Delegation edges

:   An edge from a tool to another agent's card means "this tool runs that agent". The other agent is drawn in its own row underneath, one step to the right, with its own tools and output.

    - A **dashed** edge was found by reading the tool function's code. It is a good guess.
    - A **solid** edge was seen happening in a run. It is a fact.

    See [Blueprint vs run](../concepts/blueprint-vs-run.md) for how each is found.

The inspector on the right shows the details for whatever is selected. For an agent: Overview (model, settings, retries, capabilities, warnings), Instructions, and Output. Capabilities that Pydantic AI adds to every agent are folded under a "built-in" disclosure.

## What a blueprint cannot show

A blueprint is drawn without running your code, so some things are out of reach by design:

- **The text of dynamic instructions.** A function decorated with `@agent.instructions` or `@agent.system_prompt` is listed by name, marked dynamic. Its output depends on the run; you see the resolved text on each run's model requests.
- **Tools of MCP and dynamic toolsets,** as described above.
- **Delegation that is not visible in the tool's code.** The static check looks at the names a tool function refers to. It finds `await refunds_agent.run(...)` when `refunds_agent` is a module-level or enclosing-scope variable. It does not find an agent reached through `ctx.deps`, an attribute, or a helper function. Those edges appear, solid, the first time the delegation runs.

A handful of fields (instructions, system prompts, retry limits, output mode, native tools) have no public accessor in Pydantic AI and are read from private attributes. If a Pydantic AI release changes one, that field is left empty and a warning appears on the agent card. Nothing else is affected. The [compatibility page](../reference/compatibility.md#fields-read-from-private-attributes) lists them.

## Next

Blueprints become much more useful with runs on top: [Watch live runs](live-runs.md).
