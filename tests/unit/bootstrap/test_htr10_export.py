from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import bootstrap.htr10_export as htr10_export
from bootstrap.contracts import DeploymentPaths
from bootstrap.provider_binding_transition import (
    NoEligibleProviderBindingTransitionError,
    ProviderBindingTransitionError,
)


def test_reviewed_binding_audit_view_uses_already_active_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = DeploymentPaths.from_root(tmp_path)
    active = {"adapters": [{"kind": "fixture"}]}

    def no_remaining_transition(*args: Any, **kwargs: Any) -> None:
        raise NoEligibleProviderBindingTransitionError(
            "provider-binding transition has no eligible changes"
        )

    monkeypatch.setattr(
        htr10_export,
        "prepare_live_provider_binding_transition",
        no_remaining_transition,
    )

    reviewed, transition = htr10_export._reviewed_binding_audit_view(
        paths,
        adapter_payload=b"{}",
        adapter_config=active,
        mapping={"members": []},
        test_mode=True,
    )

    assert reviewed is active
    assert transition is None


def test_reviewed_binding_audit_view_preserves_prepared_transition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = DeploymentPaths.from_root(tmp_path)
    prepared = {"adapters": [{"kind": "prepared"}]}
    public = {"contract": "provider-binding-config-transition-v1"}

    class _Transition:
        def __init__(self) -> None:
            self.prepared = prepared

        def public(self) -> dict[str, Any]:
            return public

    monkeypatch.setattr(
        htr10_export,
        "prepare_live_provider_binding_transition",
        lambda *args, **kwargs: _Transition(),
    )

    reviewed, transition = htr10_export._reviewed_binding_audit_view(
        paths,
        adapter_payload=b"{}",
        adapter_config={"adapters": []},
        mapping={"members": []},
        test_mode=True,
    )

    assert reviewed is prepared
    assert transition is public


def test_reviewed_binding_audit_view_rejects_other_transition_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = DeploymentPaths.from_root(tmp_path)

    def ambiguous_transition(*args: Any, **kwargs: Any) -> None:
        raise ProviderBindingTransitionError("ambiguous reviewed binding")

    monkeypatch.setattr(
        htr10_export,
        "prepare_live_provider_binding_transition",
        ambiguous_transition,
    )

    with pytest.raises(
        ProviderBindingTransitionError, match="ambiguous reviewed binding"
    ):
        htr10_export._reviewed_binding_audit_view(
            paths,
            adapter_payload=b"{}",
            adapter_config={"adapters": []},
            mapping={"members": []},
            test_mode=True,
        )
