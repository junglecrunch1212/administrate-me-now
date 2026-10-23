"""Locate the supported local Tailscale CLI, including the ordinary Mac app."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def tailscale_executable() -> str:
    command = shutil.which("tailscale")
    if command:
        return command
    app = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
    if app.is_file() and os.access(app, os.X_OK):
        return str(app)
    # Preserve the ordinary subprocess missing-command behavior on other hosts.
    return "tailscale"
