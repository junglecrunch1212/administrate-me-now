"""Skill runner package — thin AdministrateMe wrapper around OpenClaw's
`/tools/invoke` + `llm-task` per [ADR-0002] and [BUILD.md L4-continued]."""

from __future__ import annotations

from adminme.lib.skill_runner.wrapper import (
    OpenClawResponseMalformed,
    OpenClawTimeout,
    OpenClawUnreachable,
    SkillContext,
    SkillHandlerError,
    SkillInputInvalid,
    SkillOutputInvalid,
    SkillResult,
    SkillScopeInsufficient,
    SkillSensitivityRefused,
    run_skill,
    set_default_event_log,
)

__all__ = [
    "OpenClawResponseMalformed",
    "OpenClawTimeout",
    "OpenClawUnreachable",
    "SkillContext",
    "SkillHandlerError",
    "SkillInputInvalid",
    "SkillOutputInvalid",
    "SkillResult",
    "SkillScopeInsufficient",
    "SkillSensitivityRefused",
    "run_skill",
    "set_default_event_log",
]
