"""Command-line entrypoint for the credential-free source stress proof."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from adminme.simulations.source_pipeline_stress import run_source_pipeline_stress


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("fast", "extended"), default="fast")
    parser.add_argument("--instance-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.instance_dir is None:
        with tempfile.TemporaryDirectory(prefix="adminme-source-stress-") as raw:
            result = run_source_pipeline_stress(
                args.profile,
                instance_dir=Path(raw) / "instance",
                repository_root=REPOSITORY_ROOT,
            )
    else:
        result = run_source_pipeline_stress(
            args.profile,
            instance_dir=args.instance_dir,
            repository_root=REPOSITORY_ROOT,
        )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
