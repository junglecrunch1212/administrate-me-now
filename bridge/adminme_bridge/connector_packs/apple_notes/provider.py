"""Supported macOS Notes automation boundary.

Apple publishes no Notes data framework comparable to EventKit.  The bridge
therefore uses the Notes application's own scripting dictionary via JXA and
never reads or writes Notes' private SQLite/CoreData store.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime, timezone
from typing import Any, Callable

from adminme_bridge.knowledge_sources import KnowledgeDocument, KnowledgeFolder


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


_JXA = r"""
function safe(fn, fallback) { try { const v = fn(); return v === undefined ? fallback : v; } catch (_) { return fallback; } }
function iso(value) { try { return value ? new Date(value).toISOString() : null; } catch (_) { return null; } }
function idOf(value) { return String(safe(() => value.id(), "")); }
function foldersOf(container) { return safe(() => container.folders(), []); }
function notesOf(container) { return safe(() => container.notes(), []); }
function findAccount(app, id, name) {
  for (const account of app.accounts()) if ((!id || idOf(account) === id) && (!name || safe(() => account.name(), "") === name)) return account;
  throw new Error("Notes account not found");
}
function walkFolders(container, prefix, out) {
  for (const folder of foldersOf(container)) {
    const name = String(safe(() => folder.name(), ""));
    const path = prefix ? prefix + "/" + name : name;
    out.push({folder: folder, id: idOf(folder), path: path});
    walkFolders(folder, path, out);
  }
}
function allFolders(app) {
  const out = [];
  for (const account of app.accounts()) {
    const rows = []; walkFolders(account, "", rows);
    for (const row of rows) out.push({account: account, folder: row.folder, id: row.id, path: row.path});
  }
  return out;
}
function findFolder(app, id, path, account) {
  for (const row of allFolders(app)) if ((!account || idOf(row.account) === idOf(account)) && ((!id || row.id === id) && (!path || row.path === path))) return row.folder;
  throw new Error("Notes folder not found");
}
function findNote(app, id) {
  for (const row of allFolders(app)) for (const note of notesOf(row.folder)) if (idOf(note) === id) return {note: note, folder: row.folder, account: row.account, path: row.path};
  throw new Error("Note not found");
}
function assertRevision(note, expected) {
  if (!expected) return;
  const actual = iso(safe(() => note.modificationDate(), null));
  if (actual && actual !== expected) throw new Error("provider revision conflict: expected " + expected + ", actual " + actual);
}
function scan(app) {
  const result = []; const folderRows = allFolders(app);
  for (const row of folderRows) for (const note of notesOf(row.folder)) {
    const accountName = String(safe(() => row.account.name(), ""));
    const title = safe(() => note.name(), null);
    const modified = safe(() => note.modificationDate(), null);
    result.push({
      external_id: idOf(note), title: title, body: String(safe(() => note.body(), "")),
      body_format: "html", account_external_id: idOf(row.account), account_name: accountName,
      folder_external_id: idOf(row.folder), folder_path: row.path,
      created_at: iso(safe(() => note.creationDate(), null)),
      modified_at: iso(modified), provider_revision: iso(modified),
      locked: Boolean(safe(() => note.passwordProtected(), false)),
      shared: Boolean(safe(() => note.shared(), false)), pinned: null,
      tags: null, attachments: [], metadata: {},
      capability_warnings: ["attachments_not_scriptable", "tags_not_scriptable", "pin_not_scriptable", "collaboration_not_scriptable"]
    });
  }
  const folders = folderRows.map(row => {
    const split = row.path.lastIndexOf("/"); const parentPath = split < 0 ? null : row.path.substring(0, split);
    const parent = parentPath === null ? null : folderRows.find(candidate => idOf(candidate.account) === idOf(row.account) && candidate.path === parentPath);
    return {external_id: row.id, path: row.path, account_external_id: idOf(row.account), parent_external_id: parent ? parent.id : null};
  });
  return {documents: result, folders: folders};
}
function run(argv) {
  const req = JSON.parse(argv[0]); const app = Application("Notes"); app.includeStandardAdditions = true;
  if (req.operation === "scan") return JSON.stringify({ok: true, result: scan(app)});
  let result = null;
  if (req.operation === "create_note") {
    const account = findAccount(app, req.payload.account_external_id, req.payload.account_name);
    const folder = findFolder(app, req.payload.folder_external_id, req.payload.folder_path, account);
    const note = app.make({new: "note", at: folder, withProperties: {body: req.payload.body}});
    result = {external_id: idOf(note)};
  } else if (req.operation === "update_note" || req.operation === "append_note") {
    const row = findNote(app, req.target_external_id);
    assertRevision(row.note, req.payload.expected_provider_revision);
    const current = String(safe(() => row.note.body(), ""));
    row.note.body = req.operation === "append_note" ? current + req.payload.body : req.payload.body;
    result = {external_id: idOf(row.note)};
  } else if (req.operation === "move_note") {
    const row = findNote(app, req.target_external_id);
    assertRevision(row.note, req.payload.expected_provider_revision);
    const folder = findFolder(app, req.payload.folder_external_id, req.payload.folder_path, row.account);
    app.move(row.note, {to: folder}); result = {external_id: idOf(row.note)};
  } else if (req.operation === "delete_note") {
    const row = findNote(app, req.target_external_id); assertRevision(row.note, req.payload.expected_provider_revision); app.delete(row.note); result = {external_id: req.target_external_id};
  } else if (req.operation === "create_folder") {
    const account = findAccount(app, req.payload.account_external_id, req.payload.account_name);
    const folder = app.make({new: "folder", at: account, withProperties: {name: req.payload.folder_name}});
    result = {external_id: idOf(folder)};
  } else if (req.operation === "update_folder") {
    const folder = findFolder(app, req.target_external_id, null, null); folder.name = req.payload.folder_name; result = {external_id: idOf(folder)};
  } else if (req.operation === "delete_folder") {
    const folder = findFolder(app, req.target_external_id, null, null); app.delete(folder); result = {external_id: req.target_external_id};
  } else throw new Error("Unsupported Notes operation: " + req.operation);
  return JSON.stringify({ok: true, result: result});
}
"""


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=60
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class AppleNotesScriptingProvider:
    def __init__(self, *, runner: CommandRunner | None = None) -> None:
        self._runner = runner or _default_runner
        self._last_folders: list[KnowledgeFolder] = []

    def _invoke(
        self, operation: str, payload: dict[str, Any], target: str | None = None
    ) -> Any:
        request = json.dumps(
            {"operation": operation, "payload": payload, "target_external_id": target},
            separators=(",", ":"),
        )
        completed = self._runner(
            ["osascript", "-l", "JavaScript", "-e", _JXA, "--", request]
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Apple Notes automation failed: {completed.stderr.strip()[:1000]}"
            )
        response = json.loads(completed.stdout)
        if not response.get("ok"):
            raise RuntimeError(
                str(response.get("error") or "Apple Notes automation failed")
            )
        return response.get("result")

    async def documents(self) -> list[KnowledgeDocument]:
        snapshot = await asyncio.to_thread(self._invoke, "scan", {})
        rows = snapshot.get("documents", []) if isinstance(snapshot, dict) else snapshot
        folder_rows = snapshot.get("folders", []) if isinstance(snapshot, dict) else []
        by_path = {
            (row.get("account_external_id"), row.get("path")): row.get("external_id")
            for row in folder_rows
        }
        self._last_folders = [
            KnowledgeFolder(
                external_id=str(row["external_id"]),
                path=str(row["path"]),
                account_external_id=row.get("account_external_id"),
                parent_external_id=(
                    row.get("parent_external_id")
                    or by_path.get(
                        (
                            row.get("account_external_id"),
                            str(row.get("path") or "").rsplit("/", 1)[0],
                        )
                    )
                ),
            )
            for row in folder_rows
            if row.get("external_id") and row.get("path")
        ]
        documents: list[KnowledgeDocument] = []
        for row in rows or []:
            modified = _parse_datetime(row.get("modified_at")) or datetime.now(
                timezone.utc
            )
            locked = bool(row.get("locked"))
            body = str(row.get("body") or "")
            if not body:
                body = (
                    "<p>[Locked note contents unavailable to automation]</p>"
                    if locked
                    else "<p></p>"
                )
            warnings = list(row.get("capability_warnings") or [])
            if locked and "locked_note_contents_unavailable" not in warnings:
                warnings.append("locked_note_contents_unavailable")
            documents.append(
                KnowledgeDocument(
                    external_id=str(row["external_id"]),
                    title=row.get("title"),
                    body=body,
                    captured_at=modified,
                    body_format="html",
                    account_external_id=row.get("account_external_id"),
                    account_name=row.get("account_name"),
                    folder_external_id=row.get("folder_external_id"),
                    folder_path=row.get("folder_path"),
                    created_at=_parse_datetime(row.get("created_at")),
                    provider_modified_at=modified,
                    provider_revision=row.get("provider_revision"),
                    locked=locked,
                    shared=bool(row.get("shared")),
                    pinned=row.get("pinned"),
                    tags=row.get("tags"),
                    attachments=list(row.get("attachments") or []),
                    metadata=dict(row.get("metadata") or {}),
                    capability_warnings=warnings,
                )
            )
        return documents

    async def folders(self) -> list[KnowledgeFolder]:
        return list(self._last_folders)

    async def apply_write(
        self, operation: str, payload: dict[str, Any], target_external_id: str | None
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(
            self._invoke, operation, payload, target_external_id
        )
        return dict(result or {})


__all__ = ["AppleNotesScriptingProvider", "CommandRunner"]
