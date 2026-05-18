"""adminme-bridge — L1-bridge daemon package.

Per memo 04 §2 / [D17] / [§6.19] (bridge sovereignty). Sibling Poetry
package to ``adminme`` per [PM-29]; imports event schemas from
``adminme.events.schemas.bridge`` via editable install. 11c-i ships the
daemon shell only; concrete adapters land in 11c-ii.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
