"""calendaring-apple-calendar-bridge connector-pack (11b-ii-β-β).

Bridge-runtime deployment of the Apple Calendar dual-deployment adapter.
The entrypoint :class:`AppleCalendarBridgeAdapter` (wrapper.py) is a THIN
wrapper around the central ``AppleCalendarAdapter`` — see ``wrapper.py``
for the composition rationale per [PM-34].
"""

from adminme_bridge.connector_packs.calendaring_apple_calendar_bridge.wrapper import (
    AppleCalendarBridgeAdapter,
)

__all__ = ["AppleCalendarBridgeAdapter"]
