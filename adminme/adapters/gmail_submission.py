"""Receiver-authenticated routing for reviewed direct Gmail submissions.

The Gmail API supplies the outer receiver-added Authentication-Results. A
reviewed receiver_aligned_sender route means the receiving domain authenticated
an aligned signing domain and the exact From address was separately reviewed;
it is not a cryptographic assertion about a human. Copied MIME headers, ARC,
display names and an unsigned From match never establish a route.
"""

from __future__ import annotations

from email.utils import getaddresses
import re
from typing import Any


def verified_sender_route(
    headers: list[dict[str, Any]], config: dict[str, Any]
) -> tuple[str | None, str]:
    values: dict[str, list[str]] = {}
    for header in headers:
        name, value = header.get("name"), header.get("value")
        if isinstance(name, str) and isinstance(value, str):
            values.setdefault(name.casefold(), []).append(value)

    def singleton_address(name: str) -> str | None:
        raw = values.get(name, [])
        if len(raw) != 1:
            return None
        addresses = getaddresses(raw)
        if len(addresses) != 1 or "@" not in addresses[0][1]:
            return None
        return addresses[0][1].strip().casefold()

    sender = singleton_address("from")
    recipient = singleton_address("to")
    expected_account = str(config.get("provider_account_id") or "").strip().casefold()
    if (
        not sender
        or not expected_account
        or expected_account != str(config.get("user_email") or "").strip().casefold()
        or recipient != expected_account
        or values.get("cc")
        or values.get("bcc")
        or values.get("sender")
        or values.get("resent-from")
        or values.get("resent-to")
        or values.get("list-id")
        or values.get("x-forwarded-to")
        or any(
            value.strip().casefold() != "no"
            for value in values.get("auto-submitted", [])
        )
    ):
        return None, "unverified"

    # Fail closed on injected/duplicate Authentication-Results, including an
    # attacker copying an mx.google.com result. Only the outer header block is
    # inspected; ARC results and attached-message headers have no authority.
    results = values.get("authentication-results", [])
    if len(results) != 1:
        return None, "unverified"
    result = results[0]
    if not re.match(r"^\s*mx\.google\.com\s*;", result, re.I):
        return None, "unverified"
    result_position = next(
        index
        for index, header in enumerate(headers)
        if str(header.get("name") or "").casefold() == "authentication-results"
    )
    received_before = [
        str(header.get("value") or "")
        for header in headers[:result_position]
        if str(header.get("name") or "").casefold() == "received"
    ]
    if (
        not received_before
        or re.search(r"\bby\s+mx\.google\.com(?=[\s;])", received_before[-1], re.I)
        is None
    ):
        return None, "unverified"
    # Comment contents cannot smuggle an authentication method or identity.
    uncommented = result
    for _ in range(8):
        normalized = re.sub(r"\([^()]*\)", " ", uncommented)
        if normalized == uncommented:
            break
        uncommented = normalized
    if "(" in uncommented or ")" in uncommented:
        return None, "unverified"
    clauses = [clause.strip().casefold() for clause in uncommented.split(";")[1:]]
    domain = sender.rsplit("@", 1)[1]

    def property_value(clause: str, name: str) -> str | None:
        matches = re.findall(
            r"(?:^|\s)" + re.escape(name) + r"\s*=\s*([^\s;]+)", clause
        )
        if len(matches) != 1:
            return None
        return matches[0].strip('"')

    dmarc = [clause for clause in clauses if re.match(r"dmarc\s*=", clause)]
    if (
        len(dmarc) != 1
        or not re.match(r"dmarc\s*=\s*pass(?:\s|$)", dmarc[0])
        or property_value(dmarc[0], "header.from") != domain
    ):
        return None, "unverified"
    signing_identities: list[str] = []
    for clause in clauses:
        if not re.match(r"dkim\s*=\s*pass(?:\s|$)", clause):
            continue
        identity = property_value(clause, "header.i")
        signing_domain = property_value(clause, "header.d")
        if signing_domain is None and identity and "@" in identity:
            signing_domain = identity.rsplit("@", 1)[1]
        # Exact alignment is deliberately stricter than DMARC organizational
        # domain alignment; a sibling/subdomain route requires its own review.
        if signing_domain == domain:
            signing_identities.append(identity or "@" + domain)
    if not signing_identities:
        return None, "unverified"
    matches = [
        route
        for route in config.get("trusted_sender_routes") or []
        if isinstance(route, dict)
        and str(route.get("exact_from_identifier") or "").strip().casefold() == sender
    ]
    if len(matches) != 1:
        return None, "unverified"
    route = matches[0]
    policy_name = route.get("authentication_policy")
    if policy_name == "exact_dkim_identity":
        exact_identity = str(
            route.get("exact_authenticated_identifier") or ""
        ).casefold()
        if exact_identity != sender or exact_identity not in signing_identities:
            return None, "unverified"
    elif policy_name != "receiver_aligned_sender":
        return None, "unverified"
    route_key = route.get("route_key")
    if (
        not isinstance(route_key, str)
        or re.fullmatch(r"route:[0-9a-f]{32,64}", route_key) is None
    ):
        return None, "unverified"
    return route_key, str(policy_name)
