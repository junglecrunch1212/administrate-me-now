"""
Shared pytest fixtures for AdministrateMe tests.

Per SYSTEM_INVARIANTS.md §15: tests pass an isolated tmp path to
InstanceConfig; production code resolves through the real config; the
bootstrap wizard populates a fresh instance directory. Fixtures here will
produce the isolated tmp InstanceConfig used by unit and integration tests.

Per SYSTEM_INVARIANTS.md §12: test fixtures are subject to the
no-hardcoded-identity rule EXCEPT when a fixture is explicitly flagged with
`# fixture:tenant_data:ok` (the canary at tests/unit/test_no_hardcoded_
identity.py will exempt those lines).

Stub for now. Prompt 03 will add the first real fixture (a tmp
InstanceConfig).
"""

import pytest


@pytest.fixture
def unreleased_finance_advisor(monkeypatch):
    """Held advisor unit/contracts only; never proof of the owner release gate.

    No production fixture flag enables this ceiling. Test modules requesting
    this fixture explicitly exercise unreleased advisor code in isolation.
    test_finance_owner_release and its production driver must not request it.
    """
    from adminme.finance import release

    monkeypatch.setattr(release, "ADVISOR_AVAILABLE", True)
