"""Apple Contacts member-bridge adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from typing import Any, ClassVar

from adminme.lib.adapters import WriteOperation, WriteResult
from adminme.lib.adapters.contacts_base import (
    ClientFactory,
    ContactMirrorAdapter as ExternalStateMirrorAdapter,
    normalize_identifier,
)


def _value(obj: Any, name: str, default: Any = None) -> Any:
    candidate = getattr(obj, name, default)
    return candidate() if callable(candidate) else candidate


def _framework() -> Any:
    try:
        import Contacts  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised on bridge macOS gate
        raise RuntimeError("Apple Contacts.framework binding is unavailable") from exc
    return Contacts


def _error_result(value: Any) -> tuple[Any, Any | None]:
    if isinstance(value, tuple) and len(value) == 2:
        return value[0], value[1]
    return value, None


class MacOSContactsClient:
    """Narrow Contacts.framework client used only by the member bridge."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = dict(config)
        self._contacts = _framework()
        self._store = self._contacts.CNContactStore.alloc().init()

    def _request_access(self) -> None:
        contacts = self._contacts
        entity = contacts.CNEntityTypeContacts
        status = contacts.CNContactStore.authorizationStatusForEntityType_(entity)
        if status == contacts.CNAuthorizationStatusAuthorized:
            return
        completed = threading.Event()
        outcome: dict[str, Any] = {}

        def callback(granted: bool, error: Any) -> None:
            outcome.update({"granted": bool(granted), "error": error})
            completed.set()

        self._store.requestAccessForEntityType_completionHandler_(entity, callback)
        if not completed.wait(timeout=120):
            raise TimeoutError("Contacts permission request did not complete")
        if not outcome.get("granted"):
            raise PermissionError("Contacts permission was not granted")

    def _keys(self) -> list[Any]:
        contacts = self._contacts
        return [
            contacts.CNContactIdentifierKey,
            contacts.CNContactTypeKey,
            contacts.CNContactGivenNameKey,
            contacts.CNContactFamilyNameKey,
            contacts.CNContactNicknameKey,
            contacts.CNContactOrganizationNameKey,
            contacts.CNContactJobTitleKey,
            contacts.CNContactEmailAddressesKey,
            contacts.CNContactPhoneNumbersKey,
            contacts.CNContactUrlAddressesKey,
        ]

    @staticmethod
    def _labeled(values: Any, kind: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in values or []:
            raw = _value(item, "value", "")
            if kind == "phone":
                raw = _value(raw, "stringValue", raw)
            text = str(raw or "")
            if text:
                result.append(
                    {
                        "kind": kind,
                        "value": text,
                        "label": str(_value(item, "label", "") or "") or None,
                    }
                )
        return result

    def _record(self, contact: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "external_id": str(_value(contact, "identifier", "")),
            "kind": "organization"
            if int(_value(contact, "contactType", 0))
            == int(self._contacts.CNContactTypeOrganization)
            else "person",
            "given_name": str(_value(contact, "givenName", "") or "") or None,
            "family_name": str(_value(contact, "familyName", "") or "") or None,
            "nickname": str(_value(contact, "nickname", "") or "") or None,
            "organization_name": str(_value(contact, "organizationName", "") or "")
            or None,
            "job_title": str(_value(contact, "jobTitle", "") or "") or None,
            "identifiers": [
                *self._labeled(_value(contact, "emailAddresses", []), "email"),
                *self._labeled(_value(contact, "phoneNumbers", []), "phone"),
                *self._labeled(_value(contact, "urlAddresses", []), "url"),
            ],
            "group_external_ids": [],
            "fields": {},
        }
        record["display_name"] = " ".join(
            str(record.get(key) or "") for key in ("given_name", "family_name")
        ).strip() or str(record.get("organization_name") or "Unnamed contact")
        record["sort_name"] = (
            " ".join(
                str(record.get(key) or "") for key in ("family_name", "given_name")
            ).strip()
            or record["display_name"]
        )
        revision_material = json.dumps(
            record, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        record["revision"] = hashlib.sha256(revision_material).hexdigest()
        return record

    def _fetch(self) -> list[dict[str, Any]]:
        self._request_access()
        request = self._contacts.CNContactFetchRequest.alloc().initWithKeysToFetch_(
            self._keys()
        )
        request.setUnifyResults_(True)
        container = str(self._config.get("container_external_id") or "")
        if container and container != "all-containers":
            request.setPredicate_(
                self._contacts.CNContact.predicateForContactsInContainerWithIdentifier_(
                    container
                )
            )
        contacts: list[Any] = []

        def collect(contact: Any, _stop: Any) -> None:
            contacts.append(contact)

        completed, error = _error_result(
            self._store.enumerateContactsWithFetchRequest_error_usingBlock_(
                request, None, collect
            )
        )
        if not completed or error is not None:
            raise RuntimeError("Contacts enumeration failed")
        return [self._record(contact) for contact in contacts]

    async def changes(self, cursor: str | None) -> dict[str, Any]:
        records = await asyncio.to_thread(self._fetch)
        current = {
            str(record["external_id"]): str(record["revision"]) for record in records
        }
        prior: dict[str, str] = {}
        if cursor:
            try:
                parsed = json.loads(cursor)
                if isinstance(parsed, dict) and all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in parsed.items()
                ):
                    prior = parsed
            except json.JSONDecodeError:
                prior = {}
        deletions = [
            {"external_id": external_id, "revision": revision}
            for external_id, revision in prior.items()
            if external_id not in current
        ]
        return {
            "records": records,
            "groups": [],
            "deletions": deletions,
            "next_cursor": json.dumps(current, separators=(",", ":"), sort_keys=True),
        }

    def _fetch_one(self, external_id: str) -> Any:
        value, error = _error_result(
            self._store.unifiedContactWithIdentifier_keysToFetch_error_(
                external_id, self._keys(), None
            )
        )
        if value is None or error is not None:
            raise LookupError("contact does not exist")
        return value

    def _apply_fields(self, contact: Any, payload: dict[str, Any]) -> None:
        setters = {
            "given_name": "setGivenName_",
            "family_name": "setFamilyName_",
            "nickname": "setNickname_",
            "organization_name": "setOrganizationName_",
            "job_title": "setJobTitle_",
        }
        for key, setter in setters.items():
            if key in payload:
                getattr(contact, setter)(str(payload.get(key) or ""))

    def _apply_identifier(self, contact: Any, payload: dict[str, Any]) -> None:
        """Replace one exact observed endpoint without widening the write seam."""

        kind = str(payload.get("identifier_kind") or "")
        prior = str(payload.get("prior_value_normalized") or "")
        value = str(payload.get("value") or "").strip()
        if kind not in {"email", "phone", "imessage_handle"} or not prior or not value:
            raise ValueError("identifier correction requires an exact endpoint")
        use_phone = kind == "phone" or (kind == "imessage_handle" and "@" not in value)
        source = list(
            _value(contact, "phoneNumbers" if use_phone else "emailAddresses", []) or []
        )
        matches = []
        for index, item in enumerate(source):
            raw = _value(item, "value", "")
            if use_phone:
                raw = _value(raw, "stringValue", raw)
            normalized_kind = "phone" if use_phone else "email"
            if normalize_identifier(normalized_kind, str(raw or "")) == prior:
                matches.append(index)
        if len(matches) != 1:
            raise ValueError("identifier correction target is missing or ambiguous")
        index = matches[0]
        existing = source[index]
        label = payload.get("label")
        if label is None:
            label = _value(existing, "label", None)
        replacement_value = (
            self._contacts.CNPhoneNumber.phoneNumberWithStringValue_(value)
            if use_phone
            else value
        )
        source[index] = self._contacts.CNLabeledValue.labeledValueWithLabel_value_(
            label, replacement_value
        )
        if use_phone:
            contact.setPhoneNumbers_(source)
        else:
            contact.setEmailAddresses_(source)

    def _write_sync(
        self, kind: str, target_external_id: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._request_access()
        request = self._contacts.CNSaveRequest.alloc().init()
        if kind == "create_contact":
            contact = self._contacts.CNMutableContact.alloc().init()
            self._apply_fields(contact, payload)
            container = str(self._config.get("container_external_id") or "")
            request.addContact_toContainerWithIdentifier_(
                contact, None if container == "all-containers" else container
            )
        else:
            if not target_external_id:
                raise ValueError("contact write requires target_external_id")
            existing = self._fetch_one(target_external_id)
            expected_revision = payload.get("expected_provider_revision")
            if expected_revision != self._record(existing)["revision"]:
                raise ValueError("contact provider revision conflict")
            contact = existing.mutableCopy()
            if kind == "update_contact":
                self._apply_fields(contact, payload)
                request.updateContact_(contact)
            elif kind == "update_party_identifier":
                self._apply_identifier(contact, payload)
                request.updateContact_(contact)
            elif kind == "delete_contact":
                request.deleteContact_(contact)
            else:
                raise ValueError(f"unsupported contact operation: {kind}")
        completed, error = _error_result(
            self._store.executeSaveRequest_error_(request, None)
        )
        if not completed or error is not None:
            raise RuntimeError("Contacts save failed")
        return {"external_id": str(_value(contact, "identifier", "")) or None}

    async def write(
        self, kind: str, target_external_id: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._write_sync, kind, target_external_id, dict(payload)
        )


def macos_contacts_client_factory(config: dict[str, Any]) -> MacOSContactsClient:
    return MacOSContactsClient(config)


class AppleContactsAdapter(ExternalStateMirrorAdapter):
    name: ClassVar[str] = "contacts-apple-contacts"
    provider: ClassVar[str] = "apple_contacts"
    runtime_shape: ClassVar[str] = "member_bridge"
    capabilities: ClassVar[list[Any]] = [
        {
            "kind": "Cat-B",
            "runtime": "bridge",
            "event_families": [
                "contact.record.observed",
                "contact.record.deleted",
                "contact.group.observed",
            ],
        }
    ]
    # D23 authorizes only the sparse correction seam.  Creation and deletion
    # remain deliberately unavailable for a member's native address book.
    write_capabilities: ClassVar[list[str]] = ["update_party_identifier"]

    def __init__(
        self,
        *,
        config: dict[str, Any],
        client_factory: ClientFactory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            config=config,
            client_factory=client_factory or macos_contacts_client_factory,
            **kwargs,
        )

    async def _apply_write_impl(self, operation: WriteOperation) -> WriteResult:
        if operation.kind not in self.write_capabilities:
            raise ValueError(f"unsupported contact operation: {operation.kind}")
        return await super()._apply_write_impl(operation)


__all__ = [
    "AppleContactsAdapter",
    "MacOSContactsClient",
    "macos_contacts_client_factory",
]
