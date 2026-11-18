"""If this file were imported or executed, the test suite would know at once.

It writes a marker file, changes the environment, and then never returns. The
agent below is still there for anyone who only reads the source.
"""

import os
import pathlib
import subprocess
import sys

MARKER = pathlib.Path(os.environ.get("PVIZ_HOSTILE_MARKER", "pviz-hostile-marker.txt"))
MARKER.write_text("this file was executed", encoding="utf-8")
os.environ["PVIZ_HOSTILE_RAN"] = "1"
subprocess.run([sys.executable, "-c", "import pathlib; pathlib.Path('pviz-hostile-child.txt').write_text('x')"])

from pydantic_ai import Agent  # noqa: E402


def __getattr__(name):
    raise RuntimeError("module attribute access ran code")


class Explosive:
    def __init_subclass__(cls, **kwargs):
        raise RuntimeError("class creation ran code")

    def __repr__(self):
        raise RuntimeError("repr ran code")


suspicious = Agent(
    "test",
    name="suspicious",
    instructions=str(__import__("os").system("echo executed > pviz-hostile-shell.txt")),
    retries=eval("__import__('os').getpid()"),
    model_settings={"temperature": exec("raise SystemExit(3)")},
)


@suspicious.tool_plain
def harmless(path: str = os.path.expanduser("~")) -> str:
    """A tool whose default argument is computed.

    Args:
        path: Where to look.
    """
    return path


while True:
    pass

sys.exit(9)
