"""
Canary test: no hardcoded tenant identity in platform code.

Per SYSTEM_INVARIANTS.md §12 and DECISIONS.md §D11 (rule 4 from BUILD.md): no
family name, person's name, address, phone number, email, account number, or
medical detail appears anywhere in platform code. Tenant data lives only in
the instance directory.

This test fails CI if hardcoded tenant data appears in adminme/, bootstrap/,
profiles/, personas/, integrations/, or tests/ (except fixtures explicitly
flagged with `# fixture:tenant_data:ok`).

Implemented in a later prompt (likely prompt 08 or 17). Stub for now.
"""

import pytest


@pytest.mark.skip(reason="Implemented in a later prompt per SYSTEM_INVARIANTS.md §12")
def test_no_hardcoded_identity_in_platform_code() -> None:
    """Scan platform directories for hardcoded identity strings; fail if found."""
    pass
