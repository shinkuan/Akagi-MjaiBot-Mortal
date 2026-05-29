"""Unit tests for online_status — the online-server connection-status toasts.

Imports only ``online_status`` (no torch / libriichi), mirroring
``test_meta_show.py`` so the suite stays runnable as `python test_online_status.py`.
Per CLAUDE.md guideline 8, all data is fake.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

# Allow running this file directly: `python test_online_status.py`.
sys.path.insert(0, str(Path(__file__).parent))

import online_status  # noqa: E402


def test_offline_to_online_is_success():
    n = online_status.online_status_notification(False, True, "http://fake.invalid/")
    assert n is not None
    assert n["level"] == "success"
    assert "Connected" in n["title"]
    assert "http://fake.invalid/" in n["body"]
    assert n["id"] == online_status.ONLINE_STATUS_ID


def test_online_to_offline_is_warn():
    n = online_status.online_status_notification(True, False)
    assert n is not None
    assert n["level"] == "warn"
    assert "Disconnected" in n["title"]
    assert n["id"] == online_status.ONLINE_STATUS_ID


def test_first_connect_then_steady_then_drop_then_reconnect():
    # offline → offline (first call failed): no toast — never connected.
    assert online_status.online_status_notification(False, False) is None
    # offline → online (first success): connect toast.
    assert online_status.online_status_notification(False, True)["level"] == "success"
    # online → online (steady): no toast.
    assert online_status.online_status_notification(True, True) is None
    # online → offline (drop): disconnect toast.
    assert online_status.online_status_notification(True, False)["level"] == "warn"
    # offline → online (reconnect): connect toast again.
    assert online_status.online_status_notification(False, True)["level"] == "success"


def test_connect_body_omitted_without_server():
    n = online_status.online_status_notification(False, True)
    assert n["body"] is None


def test_notify_writes_sentinel_line_to_stderr():
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        online_status.notify("info", "Hi", "there", id="x")
    finally:
        sys.stderr = old
    line = buf.getvalue().strip()
    assert line.startswith(online_status.NOTIFY_PREFIX)
    payload = json.loads(line[len(online_status.NOTIFY_PREFIX):])
    assert payload == {"level": "info", "title": "Hi", "body": "there", "id": "x"}


def test_notify_omits_optional_fields():
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        online_status.notify("warn", "Only title")
    finally:
        sys.stderr = old
    payload = json.loads(buf.getvalue().strip()[len(online_status.NOTIFY_PREFIX):])
    assert payload == {"level": "warn", "title": "Only title"}


if __name__ == "__main__":
    # Run all `test_*` functions in declaration order.
    failed = 0
    for name in list(globals()):
        if name.startswith("test_") and callable(globals()[name]):
            try:
                globals()[name]()
                print(f"PASS  {name}")
            except AssertionError as e:
                print(f"FAIL  {name}: {e}")
                failed += 1
            except Exception as e:
                print(f"ERROR {name}: {type(e).__name__}: {e}")
                failed += 1
    if failed:
        sys.exit(1)
    print("OK")
