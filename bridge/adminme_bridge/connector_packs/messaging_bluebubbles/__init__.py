"""Pinned read-only BlueBubbles Messages connector pack."""

from adminme_bridge.connector_packs.messaging_bluebubbles.adapter import (
    BlueBubblesMessagesAdapter,
)
from adminme_bridge.connector_packs.messaging_bluebubbles.transport import (
    SELECTED_ARM64_DMG_SHA256,
    SELECTED_ARM64_DMG_SIZE,
    SELECTED_RELEASE_URL,
    SELECTED_TRANSPORT_COMMIT,
    SELECTED_TRANSPORT_VERSION,
    BlueBubblesAuthenticationError,
    BlueBubblesReadTransport,
    BlueBubblesRestTransport,
    BlueBubblesTransportError,
    KeychainCredentialResolver,
    MessagePage,
)


__all__ = [
    "BlueBubblesAuthenticationError",
    "BlueBubblesMessagesAdapter",
    "BlueBubblesReadTransport",
    "BlueBubblesRestTransport",
    "BlueBubblesTransportError",
    "KeychainCredentialResolver",
    "MessagePage",
    "SELECTED_ARM64_DMG_SHA256",
    "SELECTED_ARM64_DMG_SIZE",
    "SELECTED_RELEASE_URL",
    "SELECTED_TRANSPORT_COMMIT",
    "SELECTED_TRANSPORT_VERSION",
]
