"""
Canary test: no hardcoded instance-directory paths in platform code.

Per SYSTEM_INVARIANTS.md §15 and DECISIONS.md §D15: no module under adminme/
(or bootstrap/, profiles/, personas/, integrations/) hardcodes ~/.adminme/ or
any subpath of it as a string literal. All instance paths resolve through
InstanceConfig.

This test fails CI if `~/.adminme` or `.adminme/` appears as a string literal
in a non-fixture module under the enumerated directories.

Implemented in a later prompt (likely prompt 03 alongside InstanceConfig).
Stub for now.
"""

import pytest


@pytest.mark.skip(reason="Implemented in a later prompt per SYSTEM_INVARIANTS.md §15")
def test_no_hardcoded_instance_path_in_platform_code() -> None:
    """Grep platform directories for hardcoded instance paths; fail if found."""
    pass
