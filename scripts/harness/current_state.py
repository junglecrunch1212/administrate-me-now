"""Generate and validate a concise index without flattening constitutional history."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from .core import HarnessError, executable_inventory, git_lines, repository_root

CURRENT_STATE_PATH = Path("docs/refactor/CURRENT-STATE.md")
CASCADE_OUTPUTS = frozenset(
    {
        "docs/refactor/CONSTITUTIONAL-CROSSWALK.md",
        "docs/refactor/MIGRATION-MAP.md",
        "docs/refactor/ACCEPTANCE-CONTRACT.md",
        "docs/refactor/HANDOFF.md",
    }
)
GOVERNING_PATTERNS = (
    re.compile(r"^docs/refactor/ADR-[0-9]{4}-.*\.md$"),
    re.compile(r"^docs/refactor/CHANGE-ORDER-[0-9]{3}-.*\.md$"),
    re.compile(
        r"^docs/refactor/(?:PRODUCT-THESIS-AND-DOMAIN-MAP|UX-BEHAVIOR-CONTRACT|BOOTSTRAP-AND-TOPOLOGY-CONTRACT)\.md$"
    ),
)
LEGACY_ACTIVE_PATHS = (
    ".github/workflows/executor-cycle.yml",
    ".github/workflows/executor-cycle-c2c3c4.yml",
    ".github/workflows/executor-cycle-c3c4.yml",
    ".github/workflows/executor-c4-only.yml",
    ".github/workflows/smoke-test.yml",
    ".github/workflows/constitution-sync.yml",
    ".claude/settings.json",
    ".claude/mcp.json",
    ".claude/hooks/post-edit-verify.sh",
    ".claude/hooks/pre-bash-spend-check.sh",
    ".claude/hooks/stop-checkpoint-pause.sh",
    ".claude/agents/preflight.md",
    ".claude/agents/splitter.md",
    ".claude/agents/executor-c1.md",
    ".claude/agents/executor-c2.md",
    ".claude/agents/executor-c3.md",
    ".claude/agents/executor-c4.md",
    "validation/setup-complete.sh",
)


@dataclass(frozen=True)
class ConsistencyFinding:
    finding_id: str
    status: str
    detail: str


def _read(root: Path, relative: str) -> str:
    path = root / relative
    return path.read_text("utf-8") if path.is_file() else ""


def current_capabilities() -> tuple[tuple[str, str, str], ...]:
    return (
        (
            "v2 constitutional composition",
            "ACCEPTED",
            "docs/refactor/ADR-0000-CONSTITUTIONAL-RECONCILIATION.md",
        ),
        (
            "ten adult surfaces and three experience modes",
            "ACCEPTED",
            "docs/refactor/UX-BEHAVIOR-CONTRACT.md",
        ),
        (
            "CO-016 Triage queue continuity",
            "RELEASED_IN_OBSERVATION",
            "docs/refactor/CHANGE-ORDER-016-TRIAGE-QUEUE-CONTINUITY.md",
        ),
        (
            "CO-017 source-assessment recovery",
            "RELEASED_IN_OBSERVATION",
            "docs/refactor/CHANGE-ORDER-017-SOURCE-ASSESSMENT-RECOVERY.md",
        ),
        (
            "CO-018 source time and context correction",
            "CANDIDATE_ONLY",
            "docs/refactor/CHANGE-ORDER-018-SOURCE-TIME-AND-CONTEXT-CORRECTION.md",
        ),
        (
            "staged durable Outcome Harness recovery",
            "CANDIDATE_ONLY",
            "docs/refactor/ADR-0012-SEMI-GREENFIELD-CONTROL-PLANE-RECOVERY.md",
        ),
    )


def known_contradictions(root: Path) -> list[ConsistencyFinding]:
    findings: list[ConsistencyFinding] = []
    runtime = _read(root, "adminme/runtime/application.py")
    acceptance = _read(root, "docs/refactor/ACCEPTANCE-CONTRACT.md")
    if '"system",' in runtime and "six product routers" in acceptance:
        findings.append(
            ConsistencyFinding(
                "KNOWN-PRODUCT-COUNT",
                "UNDETERMINED",
                "Runtime PRODUCT_NAMES contains seven names including system, while AC-02 says six product routers; both paths remain current.",
            )
        )
    co16 = _read(root, "docs/refactor/CHANGE-ORDER-016-TRIAGE-QUEUE-CONTINUITY.md")
    crosswalk = _read(root, "docs/refactor/CONSTITUTIONAL-CROSSWALK.md")
    if "C112" in crosswalk and "MERGED AND ACTIVE IN OBSERVATION" not in co16:
        findings.append(
            ConsistencyFinding(
                "KNOWN-CO016-STATUS",
                "UNDETERMINED",
                "CO-016 retains candidate-only status text while current crosswalk C112 incorporates its semantics; release state as a separately named unit is UNDETERMINED.",
            )
        )
    return findings


def _runtime_product_names(root: Path) -> tuple[str, ...]:
    source = _read(root, "adminme/runtime/application.py")
    if not source:
        return ()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "AdministrateMeRuntime":
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "PRODUCT_NAMES"
                for target in statement.targets
            ):
                continue
            if not isinstance(statement.value, (ast.Tuple, ast.List)):
                return ()
            values: list[str] = []
            for item in statement.value.elts:
                if not isinstance(item, ast.Constant) or not isinstance(
                    item.value, str
                ):
                    return ()
                values.append(item.value)
            return tuple(values)
    return ()


def active_legacy_paths(root: Path) -> list[str]:
    return [path for path in LEGACY_ACTIVE_PATHS if (root / path).is_file()]


def _inventory_assertions(root: Path) -> list[ConsistencyFinding]:
    inventory = executable_inventory(root)
    expected_counts = {
        "surfaces": 10,
        "projections": 15,
        "adapter_categories": 5,
        "pack_kinds": 6,
        "profiles": 5,
        "personas": 4,
    }
    findings: list[ConsistencyFinding] = []
    for name, expected in expected_counts.items():
        actual = len(inventory[name])
        if actual != expected:
            findings.append(
                ConsistencyFinding(
                    f"COUNT-{name.upper()}",
                    "FAIL",
                    f"{name} count is {actual}; constitutional floor is {expected}",
                )
            )
    expected_products = (
        "core",
        "comms",
        "capture",
        "automation",
        "bridge",
        "member",
        "system",
    )
    actual_products = _runtime_product_names(root)
    if actual_products != expected_products:
        findings.append(
            ConsistencyFinding(
                "COUNT-PRODUCT-ROUTERS",
                "FAIL",
                "runtime product routers do not equal the reviewed six-function "
                "plus member-portal topology",
            )
        )
    acceptance = _read(root, "docs/refactor/ACCEPTANCE-CONTRACT.md")
    required_phrases = (
        "exactly fifteen registered projections",
        "six product-function routers plus the member portal router",
        "Six pack kinds",
        "five profiles",
        "four personas",
        "Ten adult surfaces, three modes",
    )
    for phrase in required_phrases:
        if phrase not in acceptance:
            findings.append(
                ConsistencyFinding(
                    "CONTRACT-PHRASE",
                    "FAIL",
                    f"acceptance contract no longer contains expected inventory phrase: {phrase}",
                )
            )
    return findings


def changed_paths(root: Path, base_ref: str | None = None) -> set[str]:
    paths = set(git_lines(root, "diff", "--name-only"))
    if base_ref:
        paths.update(git_lines(root, "diff", "--name-only", f"{base_ref}...HEAD"))
    return paths


def cascade_findings(
    root: Path, base_ref: str | None = None
) -> list[ConsistencyFinding]:
    paths = changed_paths(root, base_ref)
    governing = {
        path
        for path in paths
        if any(pattern.match(path) for pattern in GOVERNING_PATTERNS)
    }
    if not governing:
        return []
    missing = sorted(CASCADE_OUTPUTS - paths)
    if not missing:
        return []
    return [
        ConsistencyFinding(
            "GOVERNING-CASCADE",
            "FAIL",
            f"governing changes {sorted(governing)} lack required updates {missing}",
        )
    ]


def skill_reference_findings(root: Path) -> list[ConsistencyFinding]:
    from .skill_references import check_skill_references

    if not (root / ".agents" / "skills").is_dir():
        return []
    stale = check_skill_references(root)
    return [
        ConsistencyFinding(
            "SKILL-REFERENCE-DRIFT",
            "FAIL",
            f"generated reference is stale: {path}",
        )
        for path in stale
    ]


def all_findings(root: Path, base_ref: str | None = None) -> list[ConsistencyFinding]:
    legacy = active_legacy_paths(root)
    legacy_findings = (
        [
            ConsistencyFinding(
                "LEGACY-ACTIVE-AUTHORITY",
                "DRIFT",
                f"legacy prompt/executor authority remains active at {legacy}",
            )
        ]
        if legacy
        else []
    )
    return [
        *_inventory_assertions(root),
        *cascade_findings(root, base_ref),
        *skill_reference_findings(root),
        *legacy_findings,
        *known_contradictions(root),
    ]


def generate_current_state(root: Path | None = None) -> str:
    base = root or repository_root()
    inventory = executable_inventory(base)
    contradictions = known_contradictions(base)
    legacy = active_legacy_paths(base)
    lines = [
        "# AdministrateMe current state",
        "",
        "<!-- Generated by `python -m scripts.harness.cli current-state`. Do not hand-edit. -->",
        "",
        "This index points to current governing evidence without replacing historical documents. A status of `CANDIDATE_ONLY` or `UNDETERMINED` must not be described as released behavior.",
        "",
        "## Capability status",
        "",
        "| Capability | Status | Governing evidence |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| {name} | {status} | [{source}]({source.removeprefix('docs/refactor/')}) |"
        for name, status, source in current_capabilities()
    )
    lines.extend(["", "## Executable inventories", ""])
    labels = {
        "surfaces": "Adult surfaces",
        "projections": "Projections",
        "adapter_categories": "Adapter categories",
        "pack_kinds": "Pack kinds",
        "profiles": "Built-in profiles",
        "personas": "Built-in personas",
    }
    for name in (
        "surfaces",
        "projections",
        "adapter_categories",
        "pack_kinds",
        "profiles",
        "personas",
    ):
        values = inventory[name]
        lines.append(f"- {labels[name]} ({len(values)}): {', '.join(values)}")
    lines.extend(
        [
            "",
            "Six product functions and the seventh member portal router are explicitly distinguished by AC-02.",
            "",
            "## Governing current sources",
            "",
            "- [Constitutional reconciliation](ADR-0000-CONSTITUTIONAL-RECONCILIATION.md)",
            "- [Product thesis and domain map](PRODUCT-THESIS-AND-DOMAIN-MAP.md)",
            "- [UX behavior contract](UX-BEHAVIOR-CONTRACT.md)",
            "- [Acceptance contract](ACCEPTANCE-CONTRACT.md)",
            "- [Constitutional crosswalk](CONSTITUTIONAL-CROSSWALK.md)",
            "- [Migration map](MIGRATION-MAP.md)",
            "- [Handoff and release evidence](HANDOFF.md)",
            "",
            "## Known contradictions",
            "",
        ]
    )
    if contradictions:
        lines.extend(
            f"- **{finding.status} — {finding.finding_id}:** {finding.detail}"
            for finding in contradictions
        )
    else:
        lines.append("- None detected by the reviewed checks.")
    lines.extend(["", "## Active legacy automation", ""])
    if legacy:
        lines.append(
            "The following paths are detected as legacy active authority until replacement parity and retirement are proved:"
        )
        lines.append("")
        lines.extend(f"- `{path}`" for path in legacy)
    else:
        lines.append(
            "No legacy Claude/prompt executor path is active. Historical provenance is indexed in [LEGACY-EXECUTOR-HISTORY.md](LEGACY-EXECUTOR-HISTORY.md)."
        )
    lines.extend(
        [
            "",
            "## Provenance-only sources",
            "",
            "Historical root documents, `prompts/`, old executor runbooks, and retained build/release receipts remain evidence. They do not override `docs/refactor/`, executable acceptance, or the current repository instructions.",
            "",
            "## Evidence boundary",
            "",
            "Executable tests prove local candidate or fixture behavior unless an exact release receipt says live. Hosted review is `NOT_CONFIGURED` when its secret and repository policy are absent. Nothing in this index grants merge, deployment, provider-write, or live-household authority.",
            "",
        ]
    )
    return "\n".join(lines)


def write_current_state(root: Path | None = None) -> Path:
    base = root or repository_root()
    path = base / CURRENT_STATE_PATH
    path.write_text(generate_current_state(base), encoding="utf-8")
    return path


def check_current_state(root: Path | None = None) -> None:
    base = root or repository_root()
    path = base / CURRENT_STATE_PATH
    if not path.is_file():
        raise HarnessError(
            f"generated current-state index is missing: {CURRENT_STATE_PATH}"
        )
    expected = generate_current_state(base)
    actual = path.read_text("utf-8")
    if actual != expected:
        raise HarnessError(
            "docs/refactor/CURRENT-STATE.md is stale; run `python -m scripts.harness.cli current-state`"
        )
