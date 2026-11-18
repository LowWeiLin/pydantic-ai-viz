"""pydantic-ai-viz: see what your Pydantic AI agents are and what they do.

Unofficial community project, not affiliated with or endorsed by Pydantic.

The whole public API is importable from here:

- `serve` captures runs and shows them live in a local viewer.
- `record` captures runs without a server.
- `blueprint` describes an agent or a graph as data, without running it.
- `export_html` writes a single-file HTML export.
- `run_graph` runs a pydantic-graph `Graph` so its nodes are captured.
- `Session` is what `serve` and `record` return.
- `AgentBlueprint` and `GraphBlueprint` are the models `blueprint` returns.
"""

from ._export import export_html
from ._graph import run_graph
from ._introspect import blueprint
from ._models import AgentBlueprint, GraphBlueprint
from ._server import serve
from ._session import Session, record
from ._version import __version__

__all__ = [
    "AgentBlueprint",
    "GraphBlueprint",
    "Session",
    "__version__",
    "blueprint",
    "export_html",
    "record",
    "run_graph",
    "serve",
]
