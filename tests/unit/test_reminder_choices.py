"""A durable pending assignment never becomes an old execution-ticket renewal."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
import pytest
from pydantic import ValidationError

from adminme.events.schemas.reminder_choices import (
    ReminderChoiceFinishedV1,
    ReminderChoiceOfferedV1,
    ReminderListChoice,
    ReminderChoiceSelectionV1,
)
from adminme.reminder_choices import (
    choice_task_id,
    digest,
    execution_expiry,
    selected_choice,
    selected_task,
)

NOW = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)


def offered(**changes) -> ReminderChoiceOfferedV1:
    return ReminderChoiceOfferedV1.model_validate(
        {
            "member_id": "member-a",
            "task_id": "cos-reminder-choice:fixture",
            "action_id": "native-reminder:fixture",
            "request_id": "fixture-request",
            "original_source_event_id": "original-authenticated-instruction",
            "original_source_fingerprint": "a" * 64,
            "original_submitted_at": NOW - timedelta(days=1, minutes=2),
            "draft_fingerprint": "b" * 64,
            "provider": "apple_reminders",
            "operation": "create_reminder",
            "resource_owner": "requester",
            "provider_account_id": "fixture-account",
            "title": "Call a relative",
            "due_at": NOW + timedelta(days=2),
            "notes": None,
            "choices": [
                {"list_id": "fixture-personal", "label": "Personal"},
                {"list_id": "fixture-work", "label": "Work"},
            ],
            "offered_at": NOW - timedelta(days=1),
            "pending_expires_at": NOW + timedelta(days=2),
            "expiry_reason": "requested_reminder_due_time",
            **changes,
        }
    )


def test_fresh_answer_uses_durable_assignment_after_original_input_ticket_expired():
    pending = offered()
    result = selected_choice(
        pending,
        " Personal ",
        provider_account_id=pending.provider_account_id,
        current_choices=pending.choices,
        now=NOW,
    )
    assert result.list_id == "fixture-personal"
    assert pending.original_submitted_at < NOW - timedelta(hours=1)
    assert execution_expiry(pending, issued_at=NOW) == NOW + timedelta(minutes=10)
    assert pending.pending_expires_at == NOW + timedelta(days=2)


@pytest.mark.parametrize("answer", ["yes", "yes please", "do it", "both", "", "Other"])
def test_ambiguous_or_unoffered_answer_never_selects_a_destination(answer):
    pending = offered()
    with pytest.raises(HTTPException, match="") as caught:
        selected_choice(
            pending,
            answer,
            provider_account_id=pending.provider_account_id,
            current_choices=pending.choices,
            now=NOW,
        )
    assert caught.value.status_code == 409


def test_yes_requires_one_recorded_offered_choice():
    pending = offered(choices=[{"list_id": "fixture-personal", "label": "Personal"}])
    assert (
        selected_choice(
            pending,
            "yes",
            provider_account_id=pending.provider_account_id,
            current_choices=pending.choices,
            now=NOW,
        ).list_id
        == "fixture-personal"
    )
    pending = offered(
        choices=[
            {"list_id": "fixture-yes", "label": "Yes"},
            {"list_id": "fixture-no", "label": "No"},
        ]
    )
    with pytest.raises(HTTPException):
        selected_choice(
            pending,
            "yes",
            provider_account_id=pending.provider_account_id,
            current_choices=pending.choices,
            now=NOW,
        )


@pytest.mark.parametrize(
    "change", ["account", "revoked", "renamed", "duplicate_label", "expired"]
)
def test_current_destination_and_original_expiry_are_independent_admission_checks(
    change,
):
    pending = offered()
    account = (
        "different-account" if change == "account" else pending.provider_account_id
    )
    choices = pending.choices
    now = pending.pending_expires_at if change == "expired" else NOW
    if change == "revoked":
        choices = choices[1:]
    elif change == "renamed":
        choices = [ReminderListChoice(list_id="fixture-personal", label="Changed")]
    elif change == "duplicate_label":
        pending = offered(
            choices=[
                {"list_id": "fixture-one", "label": "Personal"},
                {"list_id": "fixture-two", "label": "Personal"},
            ]
        )
        choices = pending.choices
    with pytest.raises(HTTPException):
        selected_choice(
            pending,
            "Personal",
            provider_account_id=account,
            current_choices=choices,
            now=now,
        )


def test_execution_expiry_cannot_outlive_pending_due_time():
    pending = offered(
        due_at=NOW + timedelta(minutes=2), pending_expires_at=NOW + timedelta(minutes=2)
    )
    assert execution_expiry(pending, issued_at=NOW) == pending.due_at
    with pytest.raises(HTTPException):
        execution_expiry(pending, issued_at=pending.due_at)
    with pytest.raises(ValidationError):
        offered(pending_expires_at=NOW + timedelta(days=3))
    with pytest.raises(ValidationError):
        offered(offered_at=NOW + timedelta(days=3))


def test_task_identity_is_member_and_tenant_bound():
    key = choice_task_id("tenant-a", "member-a", "original-action")
    assert key == choice_task_id("tenant-a", "member-a", "original-action")
    assert key != choice_task_id("tenant-a", "member-b", "original-action")
    assert key != choice_task_id("tenant-b", "member-a", "original-action")


def test_cancellation_cannot_claim_provider_reversal_or_confirmation():
    payload = dict(
        member_id="member-a",
        task_id="task",
        action_id="action",
        offered_event_id="offer",
        answer_source_event_id="fresh-answer",
        state="cancelled",
        decided_at=NOW,
    )
    event = ReminderChoiceFinishedV1.model_validate(payload)
    assert event.provider_effect_count == 0
    with pytest.raises(ValidationError):
        ReminderChoiceFinishedV1.model_validate(
            {**payload, "domain_approval_event_id": "existing-domain"}
        )
    with pytest.raises(ValidationError):
        ReminderChoiceFinishedV1.model_validate({**payload, "state": "confirmed"})


def task_snapshot(
    *,
    second_title="Buy groceries",
    first_thread="thread-a",
    second_thread="thread-b",
    answer_thread=None,
):
    def thread(value):
        return (
            {
                "channel_id": "gmail",
                "account_id": "fixture-service-account",
                "actor_id": "trusted-member-thread:" + value,
                "thread_id": value,
            }
            if value
            else None
        )

    return ReminderChoiceSelectionV1.model_validate(
        {
            "answer_source_event_id": "fresh-answer",
            "answer_submitted_at": NOW,
            "answer_conversation": thread(answer_thread),
            "eligible_tasks": [
                {
                    "offered_event_id": "offer-a",
                    "task_id": "task-a",
                    "title_fingerprint": digest("call a relative"),
                    "pending_expires_at": NOW + timedelta(days=2),
                    "conversation": thread(first_thread),
                },
                {
                    "offered_event_id": "offer-b",
                    "task_id": "task-b",
                    "title_fingerprint": digest(second_title.strip().lower()),
                    "pending_expires_at": NOW + timedelta(days=2),
                    "conversation": thread(second_thread),
                },
            ],
        }
    )


def test_bare_cross_channel_answer_cannot_choose_between_pending_tasks():
    assert selected_task(task_snapshot(), "Personal") is None
    assert (
        selected_task(task_snapshot(answer_thread="different-thread"), "Personal")
        is None
    )
    assert (
        selected_task(
            task_snapshot(
                first_thread="thread-a",
                second_thread="thread-a",
                answer_thread="thread-a",
            ),
            "Personal",
        )
        is None
    )


def test_genuine_same_thread_selects_its_one_pending_offer():
    selected = selected_task(task_snapshot(answer_thread="thread-b"), "Personal")
    assert selected is not None and selected[0].task_id == "task-b"
    assert selected[1] == "Personal"
    snapshot = task_snapshot(answer_thread="thread-b")
    snapshot.answer_conversation.account_id = "different-service-account"
    assert selected_task(snapshot, "Personal") is None


def test_exact_authored_reference_selects_task_and_retains_literal_choice():
    for answer in ("Call a relative: Personal", "task-a: Personal"):
        selected = selected_task(task_snapshot(), answer)
        assert selected is not None and selected[0].task_id == "task-a"
        assert selected[1] == "Personal"
    assert selected_task(task_snapshot(), "Call a relative perhaps: Personal") is None
    duplicate = task_snapshot(second_title="Call a relative", answer_thread="thread-a")
    assert selected_task(duplicate, "Call a relative: Personal") is None
    selected = selected_task(duplicate, "task-b: Cancel this reminder")
    assert selected is not None and selected[0].task_id == "task-b"
    assert selected[1] == "Cancel this reminder"


def test_snapshot_cannot_omit_its_original_pending_expiry_or_duplicate_identity():
    snapshot = task_snapshot().model_dump(mode="python")
    snapshot["eligible_tasks"][1]["task_id"] = "task-a"
    with pytest.raises(ValidationError):
        ReminderChoiceSelectionV1.model_validate(snapshot)
    snapshot = task_snapshot().model_dump(mode="python")
    snapshot["eligible_tasks"][0]["pending_expires_at"] = NOW
    with pytest.raises(ValidationError):
        ReminderChoiceSelectionV1.model_validate(snapshot)
