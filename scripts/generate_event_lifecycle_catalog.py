#!/usr/bin/env python3
"""Generate or verify the reviewed whole-life lifecycle catalog artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from adminme.lifecycle_catalog import catalog_document


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    REPOSITORY_ROOT / "docs" / "refactor" / "evidence" / "event-lifecycle-catalog.json"
)


def rendered_catalog() -> str:
    return json.dumps(catalog_document(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = rendered_catalog()
    if args.check:
        if not args.output.is_file():
            raise SystemExit(f"missing generated lifecycle catalog: {args.output}")
        if args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(
                "generated lifecycle catalog is stale; run "
                "poetry run python scripts/generate_event_lifecycle_catalog.py"
            )
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
