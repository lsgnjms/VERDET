"""VERDET — Phase 0 signing spike, PC side.

Save as: gateway/verify_test.py

Reads the Mega's output over serial (opening the port resets the Mega, so it
prints everything again), then checks the signature with PyNaCl:
  1. the real message  -> must verify  (True)
  2. one character changed -> must fail (False)

Usage:
  python gateway/verify_test.py --port COM3            # Windows
  python gateway/verify_test.py --port /dev/ttyACM0    # Linux
  # or paste values from the Serial Monitor instead of using the port:
  python gateway/verify_test.py --pub <hex> --msg "<text>" --sig <hex>

Requires: pip install pynacl pyserial
"""

import argparse
import sys

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey


def read_from_serial(port: str, baud: int = 115200, timeout: float = 30.0) -> dict:
    """Collect KEY:VALUE lines from the Mega until it prints DONE."""
    import serial  # imported here so --pub/--msg/--sig mode works without pyserial

    fields: dict[str, str] = {}
    with serial.Serial(port, baud, timeout=timeout) as ser:
        while True:
            line = ser.readline().decode(errors="replace").strip()
            if not line:
                raise TimeoutError(
                    "No output from the Mega. Check the port, the baud rate (115200), "
                    "and that the Serial Monitor is closed."
                )
            print(f"  mega> {line}")
            if line == "DONE":
                return fields
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key] = value


def signature_valid(pub_hex: str, msg: str, sig_hex: str) -> bool:
    """True if sig is a valid Ed25519 signature of msg under pub."""
    try:
        VerifyKey(bytes.fromhex(pub_hex)).verify(msg.encode("utf-8"), bytes.fromhex(sig_hex))
        return True
    except BadSignatureError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the Mega's Ed25519 signature.")
    parser.add_argument("--port", help="Serial port of the Mega, e.g. COM3 or /dev/ttyACM0")
    parser.add_argument("--pub", help="Public key hex (manual mode)")
    parser.add_argument("--msg", help="Signed message (manual mode)")
    parser.add_argument("--sig", help="Signature hex (manual mode)")
    args = parser.parse_args()

    if args.port:
        fields = read_from_serial(args.port)
        pub, msg, sig = fields.get("PUB"), fields.get("MSG"), fields.get("SIG")
        print(f"\nSign time on Mega: {fields.get('SIGN_MS', '?')} ms | "
              f"free RAM: {fields.get('FREE_RAM', '?')} bytes | "
              f"Mega self-check: {fields.get('SELF_VERIFY', '?')}")
    else:
        pub, msg, sig = args.pub, args.msg, args.sig

    if not (pub and msg and sig):
        print("Missing PUB, MSG or SIG. Use --port, or all of --pub/--msg/--sig.")
        return 2

    tampered = msg[:-1] + ("1" if msg[-1] != "1" else "2")

    real_ok = signature_valid(pub, msg, sig)
    tampered_ok = signature_valid(pub, tampered, sig)

    print(f"\nReal message verifies:     {real_ok}   (expected True)")
    print(f"Tampered message verifies: {tampered_ok}   (expected False)")

    if real_ok and not tampered_ok:
        print("\nPASS: Mega signatures are compatible with PyNaCl.")
        return 0
    print("\nFAIL: see above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
