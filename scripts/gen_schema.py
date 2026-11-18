"""Write ``schema/pviz.schema.json`` from the wire models.

Usage::

    uv run python scripts/gen_schema.py          # rewrite the file
    uv run python scripts/gen_schema.py --check  # exit 1 if the file is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic_ai_viz._models import envelope_json_schema

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "pviz.schema.json"


def render() -> str:
    return json.dumps(envelope_json_schema(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write schema/pviz.schema.json from the wire models.")
    parser.add_argument("--check", action="store_true", help="do not write; fail if the committed file differs")
    args = parser.parse_args(argv)

    text = render()
    if args.check:
        current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
        if current.replace("\r\n", "\n") != text:
            print(f"{SCHEMA_PATH} is stale; run: uv run python scripts/gen_schema.py", file=sys.stderr)
            return 1
        return 0

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SCHEMA_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"wrote {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
