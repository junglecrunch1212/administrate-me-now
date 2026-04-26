"""Skill runner package — thin AdministrateMe wrapper around OpenClaw's
`/tools/invoke` + `llm-task` per [ADR-0002] and [BUILD.md L4-continued].

Public surface (`run_skill`, `SkillContext`, `SkillResult`, exceptions,
`set_default_event_log`) is filled in by `wrapper.py` in 09a/3.
"""

from __future__ import annotations
