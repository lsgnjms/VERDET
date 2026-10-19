"""Phase 4: the serial bridge. Run: pytest tests/test_serial_bridge.py"""

import json

import httpx
import pytest

from gateway import serial_bridge
from tests.test_verify import make_reading


@pytest.fixture(autouse=True)
def temp_log(tmp_path, monkeypatch):
    monkeypatch.setattr(serial_bridge, "LOG_PATH", tmp_path / "readings.jsonl")


def as_line(reading: dict) -> str:
    """How the Mega prints a reading: compact JSON plus CRLF."""
    return json.dumps(reading, separators=(",", ":")) + "\r\n"


def test_classify_reading_debug_and_noise():
    kind, data = serial_bridge.classify(as_line(make_reading(seq=3)))
    assert kind == "reading" and data["seq"] == 3
    assert serial_bridge.classify("# live V=12000 mV\r\n") == ("debug", "live V=12000 mV")
    assert serial_bridge.classify('{"meterId":"VD-0001","se')[0] == "noise"   # cut by a reset
    assert serial_bridge.classify('{"hello":1}')[0] == "noise"                # not a reading
    assert serial_bridge.classify("\x00\xff garbage")[0] == "noise"


def test_reading_is_forwarded_unchanged_and_logged():
    reading = make_reading(seq=5)
    posted = []

    def fake_post(r):
        posted.append(r)
        return {"decision": "accepted", "reasons": []}

    out = serial_bridge.handle_line(as_line(reading), fake_post)
    assert posted == [reading]                       # byte-identical values, sig included
    assert "accepted" in out
    assert serial_bridge.find_logged(5) == reading


def test_debug_lines_are_shown_not_posted():
    def fail_post(r):
        raise AssertionError("debug lines must not be posted")
    assert serial_bridge.handle_line("# KEY:LOADED", fail_post) == "  mega> KEY:LOADED"
    assert serial_bridge.handle_line("random noise", fail_post) is None


def test_app_down_still_logs_the_reading():
    def down(r):
        raise httpx.ConnectError("refused")
    out = serial_bridge.handle_line(as_line(make_reading(seq=7)), down)
    assert "--resend 7" in out
    assert serial_bridge.find_logged(7)["seq"] == 7


def test_find_logged_returns_latest_match_and_filters_meter():
    ok = lambda r: {"decision": "accepted", "reasons": []}  # noqa: E731
    serial_bridge.handle_line(as_line(make_reading(seq=1)), ok)
    serial_bridge.handle_line(as_line(make_reading(seq=1, meterId="VD-0002")), ok)
    assert serial_bridge.find_logged(1)["meterId"] == "VD-0002"
    assert serial_bridge.find_logged(1, "VD-0001")["meterId"] == "VD-0001"
    assert serial_bridge.find_logged(99) is None
