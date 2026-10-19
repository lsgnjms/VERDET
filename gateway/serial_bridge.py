"""Serial bridge: read signed readings from the Mega over USB and POST them to the app.

The bridge has no key: it can drop or delay readings, but it can't forge or
change them (the app would answer BAD_SIGNATURE). Every reading line is
forwarded exactly as received and logged to gateway/.bridge/readings.jsonl.

Usage:
  python gateway/serial_bridge.py --port COM4
  python gateway/serial_bridge.py --port COM4 --url https://your-app.onrender.com
  python gateway/serial_bridge.py --resend 12      # send logged seq 12 again (REPLAY/DUPLICATE demo)

Opening the port resets the Mega; it takes about 6 s to start measuring and
the first reading arrives about 60 s later.
"""

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import serial

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so "gateway" imports

from gateway.simulator import send  # noqa: E402

LOG_PATH = Path(__file__).resolve().parent / ".bridge" / "readings.jsonl"
BAUD = 115200


def classify(line: str) -> tuple[str, dict | str]:
    """Sort one serial line into ("reading", dict), ("debug", text) or ("noise", text)."""
    line = line.strip()
    if line.startswith("{"):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return "noise", line          # e.g. a line cut in half by a reset
        if isinstance(data, dict) and "meterId" in data and "sig" in data:
            return "reading", data
        return "noise", line
    if line.startswith("#"):
        return "debug", line.lstrip("# ").rstrip()
    return "noise", line


def log_reading(reading: dict, answer: dict | None) -> None:
    """Append the reading exactly as received, plus the app's answer."""
    LOG_PATH.parent.mkdir(exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"reading": reading, "answer": answer}) + "\n")


def find_logged(seq: int, meter_id: str | None = None) -> dict | None:
    """The most recently logged reading with this seq (and meter, if given)."""
    if not LOG_PATH.exists():
        return None
    found = None
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        reading = json.loads(line)["reading"]
        if reading["seq"] == seq and (meter_id is None or reading["meterId"] == meter_id):
            found = reading
    return found


def describe(reading: dict, answer: dict) -> str:
    reasons = ",".join(answer.get("reasons", [])) or "-"
    extra = f"  ({answer['hederaError']})" if answer.get("hederaError") else ""
    return (f"seq {reading['seq']:>5}  {reading['mWh']:>5} mWh  peak {reading['peakMw']:>6} mW"
            f"  -> {answer.get('decision')} {reasons}{extra}")


def handle_line(line: str, post: Callable[[dict], dict]) -> str | None:
    """Process one serial line. Readings are posted and logged; returns what to print."""
    kind, payload = classify(line)
    if kind == "debug":
        return f"  mega> {payload}"
    if kind == "noise":
        return None
    try:
        answer = post(payload)
    except httpx.HTTPError as exc:
        log_reading(payload, None)
        return f"seq {payload['seq']:>5}  could not reach the app ({type(exc).__name__}); logged, use --resend {payload['seq']} later"
    log_reading(payload, answer)
    return describe(payload, answer)


def run(port: str, url: str) -> None:
    """Read the meter forever, reconnecting if the USB cable is pulled."""
    post = lambda reading: send(url, reading)  # noqa: E731
    print(f"Bridge: {port} -> {url}  (Ctrl+C to stop)")
    while True:
        try:
            with serial.Serial(port, BAUD, timeout=2) as ser:
                print(f"Connected to {port}. The Mega restarts; first reading in about 70 s.")
                while True:
                    raw = ser.readline()
                    if raw:
                        out = handle_line(raw.decode("utf-8", errors="replace"), post)
                        if out:
                            print(out, flush=True)
        except serial.SerialException as exc:
            print(f"Serial port problem ({exc}). Retrying in 3 s...")
            time.sleep(3)


def resend(seq: int, url: str, meter_id: str | None) -> int:
    reading = find_logged(seq, meter_id)
    if reading is None:
        print(f"No logged reading with seq {seq} in {LOG_PATH}.")
        return 1
    print(f"Resending logged reading seq {seq} from {reading['meterId']} exactly as the meter signed it:")
    print(describe(reading, send(url, reading)))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward signed readings from the Mega to VERDET.")
    parser.add_argument("--port", help="serial port of the Mega, e.g. COM4")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="VERDET app URL")
    parser.add_argument("--resend", type=int, metavar="SEQ", help="send a logged reading again, then exit")
    parser.add_argument("--meter", help="with --resend: only match this meter ID")
    args = parser.parse_args()

    if args.resend is not None:
        return resend(args.resend, args.url, args.meter)
    if not args.port:
        parser.error("--port is required (e.g. --port COM4)")
    try:
        run(args.port, args.url)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
