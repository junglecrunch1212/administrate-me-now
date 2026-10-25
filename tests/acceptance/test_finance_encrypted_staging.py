"""Focused hosted correctness only; no large payload or performance cohort."""

import os
import sys

import pytest

from tests.acceptance.test_finance_storage_isolation import OwnedRun
from tests.fixtures.finance_storage_cases import CASES
from tests.fixtures.finance_staging_evidence import collect_owned_case

pytestmark = pytest.mark.skipif(
    os.getenv("ADMINME_SYNTHETIC_STAGING_CORRECTNESS") != "1"
    or sys.platform != "linux",
    reason="one separately admitted hosted correctness cohort only",
)


class StagedCorrectnessRun(OwnedRun):
    runtime_script = "finance_staging_cases.py"


@pytest.mark.parametrize(
    "name", (*CASES, "revoke_upload", "expiry_upload", "cancel_upload")
)
def test_staged_small_lifecycle_atomicity_authority_and_isolation(tmp_path, name):
    run = StagedCorrectnessRun(name, tmp_path / name)
    run.record["cohort"] = "focused_staging_correctness"
    run.record["production_or_native_qualification"] = False
    run.record["performance_qualification"] = False
    collect_owned_case(run)
