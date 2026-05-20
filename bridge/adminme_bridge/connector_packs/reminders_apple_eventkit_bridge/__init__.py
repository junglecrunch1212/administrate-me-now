"""reminders-apple-eventkit-bridge connector-pack (11b-ii-α-2).

Bridge-runtime deployment of the Apple Reminders dual-deployment adapter.
The entrypoint :class:`AppleEventKitBridgeAdapter` (wrapper.py) is a THIN
wrapper around the central ``AppleEventKitAdapter`` — see ``wrapper.py``
for the composition rationale per [PM-34].
"""

from adminme_bridge.connector_packs.reminders_apple_eventkit_bridge.wrapper import (
    AppleEventKitBridgeAdapter,
)

__all__ = ["AppleEventKitBridgeAdapter"]
