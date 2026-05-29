"""Online-server connection-status toast notifications.

Mortal can offload inference to an online server (see ``model.ot_settings``).
When that connection comes up or drops, this module surfaces a toast in the
Akagi UI through the host's stderr notification protocol: a stderr line
prefixed with :data:`NOTIFY_PREFIX` followed by a JSON object is parsed by
Akagi and shown as a bottom-right toast; any other stderr line is treated as
an ordinary log line.

Kept deliberately free of heavy imports (no torch / libriichi) so the
transition logic stays unit-testable on its own — see ``test_online_status.py``.
"""
from __future__ import annotations

import json
import sys

# Sentinel recognised by the Akagi host; the remainder of the line must be a
# JSON notification object. Keep in sync with the host's notification protocol.
NOTIFY_PREFIX = "@@AKAGI_NOTIFY@@ "

# Stable toast id so the "connected" / "disconnected" toasts replace one
# another instead of stacking — the toast always reflects the current status.
ONLINE_STATUS_ID = "mortal-online-status"


def notify(
    level: str,
    title: str,
    body: str | None = None,
    *,
    id: str | None = None,
) -> None:
    """Emit one toast notification to the Akagi host over stderr.

    ``level`` is one of ``"info"``, ``"success"``, ``"warn"``, ``"error"``.
    """
    payload: dict[str, object] = {"level": level, "title": title}
    if body is not None:
        payload["body"] = body
    if id is not None:
        payload["id"] = id
    sys.stderr.write(NOTIFY_PREFIX + json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stderr.flush()


def online_status_notification(
    prev: bool,
    now: bool,
    server: str | None = None,
) -> dict | None:
    """Return :func:`notify` kwargs for an online-status edge, or ``None`` when
    the status is unchanged.

    - offline → online: a "Connected" *success* toast. Fires on the first
      successful server call, and again after any reconnect.
    - online → offline: a "Disconnected" *warn* toast. The bot transparently
      falls back to the local model, so this is a degradation, not a crash.

    No notification on a steady state (``prev == now``) — in particular none on
    a first-ever *failed* connection (offline → offline).
    """
    if now and not prev:
        return {
            "level": "success",
            "title": "Connected to online server",
            "body": f"Using {server}." if server else None,
            "id": ONLINE_STATUS_ID,
        }
    if prev and not now:
        return {
            "level": "warn",
            "title": "Disconnected from online server",
            "body": "Falling back to the local model.",
            "id": ONLINE_STATUS_ID,
        }
    return None
