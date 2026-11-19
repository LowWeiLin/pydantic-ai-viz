# Real-world examples

`view` is tried on public projects that use Pydantic AI, not only on code written for its tests. Twelve of them make up the **real-world set**: each is pinned to one commit, read from GitHub and checked against what the analyser must find in it. None of their code is kept in this repository, and none of it is imported or run.

You can open any of them yourself with one command. Nothing is installed and nothing of the project runs:

```bash
pydantic-ai-viz view gh:pydantic/pydantic-ai@679ed8942dd553c5aa2da25fa777135d9a1abc49/examples
```

The first time, `git` fetches the `.py` files of that commit into a cache directory; after that the command works offline. Use `--export examples.html` to write the result to a file instead of serving it.

## The set

| Project | What it exercises | View it |
|---|---|---|
| [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai/tree/679ed8942dd553c5aa2da25fa777135d9a1abc49/examples) `examples` | Pydantic AI's own examples: tools, structured output, RAG, a bank support agent with dependencies, agents that run other agents | `gh:pydantic/pydantic-ai@679ed8942dd553c5aa2da25fa777135d9a1abc49/examples` |
| [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai/tree/78f3b7a64556d25414739fa6f2379730952ca251/examples) `examples`, at 1.0 | The same a year earlier, with a graph written as a list of node classes | `gh:pydantic/pydantic-ai@78f3b7a64556d25414739fa6f2379730952ca251/examples` |
| [pydantic/pydantic-stack-demo](https://github.com/pydantic/pydantic-stack-demo/tree/5048e75c2390f60dbff90c94669939ff4f762e14) | Temporal and DBOS wrappers around agents, deep research with three agents, an agent behind an MCP server | `gh:pydantic/pydantic-stack-demo@5048e75c2390f60dbff90c94669939ff4f762e14` |
| [pydantic/talks](https://github.com/pydantic/talks/tree/facd103259c1721e61a4e4c6cfebf973169d7bbb) | Two years of conference talks: many small scripts, MCP servers and clients, evals, two graphs | `gh:pydantic/talks@facd103259c1721e61a4e4c6cfebf973169d7bbb` |
| [coleam00/ottomator-agents](https://github.com/coleam00/ottomator-agents/tree/2be20d5737fc49aa0f665a012f3ffde283d26d54) | About forty community agents, many for early Pydantic AI releases; one agent hands work to six others | `gh:coleam00/ottomator-agents@2be20d5737fc49aa0f665a012f3ffde283d26d54` |
| [daveebbelaar/pydantic-ai-tutorial](https://github.com/daveebbelaar/pydantic-ai-tutorial/tree/248f383ae4add2dcfc25e31b696320a494767844) | One tutorial file from the first weeks of Pydantic AI: `result_type`, `Tool(...)`, a variable assigned three agents | `gh:daveebbelaar/pydantic-ai-tutorial@248f383ae4add2dcfc25e31b696320a494767844` |
| [vstorm-co/pydantic-ai-examples](https://github.com/vstorm-co/pydantic-ai-examples/tree/5d5eb325ff161c85729815573ac7521d57567987) | Tutorial scripts, one topic per directory | `gh:vstorm-co/pydantic-ai-examples@5d5eb325ff161c85729815573ac7521d57567987` |
| [the-momentum/python-ai-kit](https://github.com/the-momentum/python-ai-kit/tree/71b2d65b13b1a372e76977b9916547c90ed8a53a) | An application template: agents as classes around one `Agent` in a base class, workflows built with `GraphBuilder` | `gh:the-momentum/python-ai-kit@71b2d65b13b1a372e76977b9916547c90ed8a53a` |
| [alexliap/pydantic_deep_research_agent](https://github.com/alexliap/pydantic_deep_research_agent/tree/691d45dec24f6f61d012b93a74bd3f4290869a74) | A research pipeline: seven agents, prompts filled in with `str.format`, graphs as lists of node classes | `gh:alexliap/pydantic_deep_research_agent@691d45dec24f6f61d012b93a74bd3f4290869a74` |
| [mpfaffenberger/code_puppy](https://github.com/mpfaffenberger/code_puppy/tree/d5a84e684d915990c2cd0e27f677d792a96b627c) | A coding agent of about 800 files whose agent is assembled at run time | `gh:mpfaffenberger/code_puppy@d5a84e684d915990c2cd0e27f677d792a96b627c` |
| [vstorm-co/pydantic-deepagents](https://github.com/vstorm-co/pydantic-deepagents/tree/650b592b6accd7dc994226abbaa0cf717527e457) | A framework on Pydantic AI: `**kwargs`, toolset classes, factory functions, about twenty capabilities | `gh:vstorm-co/pydantic-deepagents@650b592b6accd7dc994226abbaa0cf717527e457` |
| [PrefectHQ/marvin](https://github.com/PrefectHQ/marvin/tree/be92af1d777c2bddf6b06a18b8afef62b3a2a844) | A library with its own `Agent` class that builds a Pydantic AI agent per run, and a Slack bot with plain Pydantic AI agents | `gh:PrefectHQ/marvin@be92af1d777c2bddf6b06a18b8afef62b3a2a844` |

The list, with what each project must yield, is [`tests/realworld/manifest.toml`](https://github.com/LowWeiLin/pydantic-ai-viz/blob/main/tests/realworld/manifest.toml). Each project declares a licence (eleven MIT, one Apache-2.0).

## Results

What the analyser found at the time of writing, as `scripts/realworld_report.py` writes it. Time is the analysis alone, with the files already fetched, on a laptop.

| Project | Commit | Files | Agents | Tools | Graphs | Delegation edges | Warnings | Time |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| pydantic/pydantic-ai `examples` | [`679ed89`](https://github.com/pydantic/pydantic-ai/tree/679ed8942dd553c5aa2da25fa777135d9a1abc49/examples) | 49 | 36 | 24 | 0 | 6 | 13 | 0.1 s |
| pydantic/pydantic-ai `examples` | [`78f3b7a`](https://github.com/pydantic/pydantic-ai/tree/78f3b7a64556d25414739fa6f2379730952ca251/examples) | 39 | 23 | 16 | 1 | 1 | 6 | 0.0 s |
| pydantic/pydantic-stack-demo | [`5048e75`](https://github.com/pydantic/pydantic-stack-demo/tree/5048e75c2390f60dbff90c94669939ff4f762e14) | 22 | 25 | 11 | 0 | 6 | 1 | 0.0 s |
| pydantic/talks | [`facd103`](https://github.com/pydantic/talks/tree/facd103259c1721e61a4e4c6cfebf973169d7bbb) | 181 | 67 | 80 | 2 | 6 | 13 | 0.2 s |
| coleam00/ottomator-agents | [`2be20d5`](https://github.com/coleam00/ottomator-agents/tree/2be20d5737fc49aa0f665a012f3ffde283d26d54) | 297 | 86 | 104 | 0 | 18 | 69 | 1.1 s |
| daveebbelaar/pydantic-ai-tutorial | [`248f383`](https://github.com/daveebbelaar/pydantic-ai-tutorial/tree/248f383ae4add2dcfc25e31b696320a494767844) | 2 | 5 | 2 | 0 | 0 | 0 | 0.0 s |
| vstorm-co/pydantic-ai-examples | [`5d5eb32`](https://github.com/vstorm-co/pydantic-ai-examples/tree/5d5eb325ff161c85729815573ac7521d57567987) | 18 | 18 | 3 | 0 | 0 | 1 | 0.0 s |
| the-momentum/python-ai-kit | [`71b2d65`](https://github.com/the-momentum/python-ai-kit/tree/71b2d65b13b1a372e76977b9916547c90ed8a53a) | 92 | 1 | 1 | 2 | 0 | 9 | 0.1 s |
| alexliap/pydantic_deep_research_agent | [`691d45d`](https://github.com/alexliap/pydantic_deep_research_agent/tree/691d45dec24f6f61d012b93a74bd3f4290869a74) | 10 | 7 | 0 | 3 | 0 | 1 | 0.0 s |
| mpfaffenberger/code_puppy | [`d5a84e6`](https://github.com/mpfaffenberger/code_puppy/tree/d5a84e684d915990c2cd0e27f677d792a96b627c) | 804 | 51 | 21 | 0 | 0 | 42 | 3.1 s |
| vstorm-co/pydantic-deepagents | [`650b592`](https://github.com/vstorm-co/pydantic-deepagents/tree/650b592b6accd7dc994226abbaa0cf717527e457) | 298 | 42 | 36 | 0 | 0 | 27 | 1.5 s |
| PrefectHQ/marvin | [`be92af1`](https://github.com/PrefectHQ/marvin/tree/be92af1d777c2bddf6b06a18b8afef62b3a2a844) | 208 | 10 | 13 | 0 | 0 | 14 | 0.5 s |
| **12 projects** | | **2,020** | **371** | **311** | **8** | **37** | **196** | **6.7 s** |

A **warning** is a value the source does not spell out, shown as its expression (see [what shows as decided at run time](concepts/how-view-works.md#what-shows-as-decided-at-run-time-and-why)). They are not errors. Of the 196, 89 are models chosen at run time, from an environment variable or a factory function, and 34 are instructions or system prompts built by a function call.

## How complete it is

The numbers above were checked against a plain text search of each repository for `Agent(` and for tool decorators, read by eye for five of the projects (talks, code_puppy, pydantic-deepagents, marvin, ottomator-agents).

- **Agents:** every `Agent(...)` construction written in code was found. Every place the text search counted that the analyser did not was inside a docstring or a string: usage examples in docstrings, a test that builds code as a string. In marvin, most of the text matches are marvin's own `Agent` class; all ten constructions of Pydantic AI's `Agent` were found.
- **Tools:** in ottomator-agents, 104 tools were found against 93 decorated ones; the rest are passed in `tools=[...]` lists. Two projects register tools in ways that cannot be placed on an agent by reading:
    - **code_puppy** has 21 tools placed on agents. A notice counts 58 more, registered by functions that are handed the agent as an argument; which agent receives them is decided when the code runs.
    - **pydantic-deepagents** has 36 tools placed. A notice counts 25 more, in 7 function toolsets that the source gives to no agent it shows.
- **Graphs:** eight are found, and each one the manifest names is checked. Graphs were not counted by a text search, so no claim is made that none is missed.

Known patterns that are not read:

- **Tools registered on an agent passed in as an argument**, as in code_puppy. They are counted in a notice, not placed.
- **Toolsets that a capability creates at run time**, for example in `__post_init__`, and returns from `get_toolset`.
- **Models from factory functions** that compute the model from their arguments or from the environment are always an expression. This is by design: see [How view works](concepts/how-view-works.md#how-values-are-resolved).
- **Agents that are not constructed in the source**: returned by a library function, an `Agent` subclass, or `Agent.from_spec(...)`.

For these, [`view --import`](reference/cli.md#view-import) on code you trust reads the live objects.

## Run it yourself

You need `git` and a network connection the first time; afterwards the pinned commits are read from the cache.

```bash
uv run pytest -m network tests/realworld
uv run python scripts/realworld_report.py --out realworld-results.md
```

The first command checks every project against its entry in the manifest. The second writes the table above; `--only ID` limits it to one project. CI runs both once a week. [CONTRIBUTING.md](contributing.md#the-real-world-set) says how to add a project.
