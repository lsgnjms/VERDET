"""One-time setup: create VERDET's HCS topics and the VKWH token on testnet.

Prints KEY=value lines to paste into .env. It never edits .env itself, and it
refuses to run if VKWH_TOKEN_ID is already set (so it can't create duplicates).

Run: .venv\\Scripts\\python.exe scripts\\setup_hedera.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so "app" imports

from app import hedera as h  # noqa: E402

TOPICS = [
    ("REGISTRY_TOPIC_ID", "VERDET registry"),
    ("READINGS_TOPIC_ID", "VERDET readings"),
    ("TRADES_TOPIC_ID", "VERDET trades"),
]


def main() -> None:
    if os.getenv("VKWH_TOKEN_ID", "").strip():
        sys.exit(
            "VKWH_TOKEN_ID is already set in .env, so setup has already run.\n"
            "To start over, clear the four ID lines in .env first."
        )

    print(f"Operator {h.operator_id()} on testnet. Creating topics and token...\n")
    lines = []
    links = []
    try:
        for env_name, memo in TOPICS:
            topic_id = h.create_topic(memo)
            lines.append(f"{env_name}={topic_id}")
            links.append(h.hashscan_topic(topic_id))
        token_id = h.create_vkwh_token()
        lines.append(f"VKWH_TOKEN_ID={token_id}")
        links.append(h.hashscan_token(token_id))
    finally:
        # Print whatever was created, even if a later step failed.
        if lines:
            print("Paste these lines into .env:\n")
            print("\n".join(lines))
            print("\nCheck them on HashScan:")
            print("\n".join(f"  {url}" for url in links))


if __name__ == "__main__":
    try:
        main()
    except h.HederaError as exc:
        sys.exit(f"\nSetup failed: {exc}")
