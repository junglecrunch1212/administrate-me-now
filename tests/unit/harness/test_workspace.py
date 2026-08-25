"""Canonical-workspace evaluation is deterministic and read-only."""

from scripts.harness.workspace import evaluate_workspace


SHA_A = "a" * 40
SHA_B = "b" * 40


def test_saved_current_recovery_workspace_passes_with_changes() -> None:
    result = evaluate_workspace(
        branch="codex/v2-control-plane-recovery",
        head_sha=SHA_A,
        local_main_sha=SHA_B,
        remote_main_sha=SHA_B,
        contains_remote_main=True,
        worktree_changes=[" M scripts/harness/core.py"],
    )

    assert result["status"] == "PASS"
    assert result["worktree_change_count"] == 1
    assert result["problems"] == []


def test_stale_direct_main_workspace_fails_with_actionable_reasons() -> None:
    result = evaluate_workspace(
        branch="main",
        head_sha=SHA_A,
        local_main_sha=SHA_A,
        remote_main_sha=SHA_B,
        contains_remote_main=False,
        worktree_changes=[],
    )

    assert result["status"] == "FAIL"
    assert result["problems"] == [
        "Development must not occur directly on main.",
        "The local origin/main tracking ref is stale; fetch before work.",
        "The candidate does not contain current GitHub main.",
    ]
