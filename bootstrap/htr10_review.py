"""Owner-gated reducer for content-free HTR-10 metadata audit evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from bootstrap.candidate_readiness import (
    CandidateReadinessError,
    record_htr10_review,
)
from bootstrap.contracts import DeploymentPaths


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Record a content-free HTR-10 receipt from reviewed attribution and "
            "source-assurance exports; this does not complete or authorize HTR-10."
        )
    )
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--historical-attribution", type=Path, required=True)
    parser.add_argument("--source-assurance", type=Path, required=True)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--confirm-reviewed", action="store_true")
    parser.add_argument("--submission-repair-manifest", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        requested_root = args.instance_root.expanduser()
        if (
            not requested_root.is_absolute()
            or not os.path.lexists(requested_root)
            or requested_root.is_symlink()
        ):
            raise CandidateReadinessError(
                "instance root must be an existing non-symlink absolute directory"
            )
        if args.confirm_reviewed is not True:
            raise CandidateReadinessError(
                "the owner must explicitly confirm review of both metadata audits"
            )
        result = record_htr10_review(
            DeploymentPaths.from_root(requested_root, production=True),
            historical_attribution=args.historical_attribution,
            source_assurance=args.source_assurance,
            observed_at=args.observed_at,
            submission_repair_manifest=args.submission_repair_manifest,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "failed", "error": type(exc).__name__, "message": str(exc)},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
