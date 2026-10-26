"""Released Finance capability; each use still requires a current book grant."""

ADVISOR_AVAILABLE = True


def advisor_unavailable() -> dict[str, str | int]:
    return {
        "state": "unavailable",
        "reason": "finance_advisor_not_released",
        "provider_effect_count": 0,
    }


def owner_books_activity(event_type: str) -> bool:
    """Keep accepted-Books metadata out of generic CoS activity, not history."""
    return event_type in {
        "settings.finance_access_changed",
        "settings.finance_import_approved",
        "settings.finance_metadata_changed",
        "finance.provenance_archived",
        "finance.publication_accepted",
        "finance.projection_verified",
        "finance.import_result",
        "finance.metadata_inspected",
        "finance.disclosure_recorded",
        "finance.advisor_accessed",
        "finance.goal_recorded",
        "finance.preference_recorded",
        "finance.decision_proposed",
        "finance.decision_approved",
        "finance.decision_outcome_recorded",
        "finance.packet_prepared",
    }
