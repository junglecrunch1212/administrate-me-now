"""Fail-closed heavy-work gate until production resource isolation is proven.

The pinned gateway does not establish reserved general-CoS capacity. Do not
start a process pool, enqueue an undurable job, or borrow EventLog's executor.
This is a readiness gate, not a scheduler or an implemented heavy-job engine.
"""

from __future__ import annotations

from typing import Any

HEAVY_JOB_LIMITS = {
    "active_workers": 1,
    "active_per_member": 1,
    "queue_size": 4,
    "wall_deadline_seconds": 120,
    "incremental_memory_bytes": 256 * 1024 * 1024,
    "simulation_paths": 2_000,
    "simulation_months": 720,
}


def finance_job_status() -> dict[str, Any]:
    return {
        "readiness": "temporarily_unavailable",
        "implementation_state": "planned",
        "reason": "heavy_finance_resource_gate_not_verified",
        "accepted": False,
        "job_id": None,
        "provider_effect_count": 0,
        "next_step": (
            "Use an admitted light calculation or prepare inputs. Heavy work needs "
            "durable event receipts, isolated cancellation and resource ceilings, "
            "and a passing mixed-load CoS capacity gate before admission."
        ),
        "proposed_limits": dict(HEAVY_JOB_LIMITS),
    }
