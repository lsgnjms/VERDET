"""Setup: create VERDET's HCS topics and the VKWH token on testnet, if missing.

Only creates what .env doesn't have yet, so it's safe to run again (e.g. to add the
`redemptions` topic later without making a second token). Prints KEY=value lines to
paste into .env; it never edits .env itself.

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
    ("REDEMPTIONS_TOPIC_ID", "VERDET redemptions"),
]


def missing(env_name: str) -> bool:
    return not os.getenv(env_name, "").strip()


def main() -> None:
    todo_topics = [(name, memo) for name, memo in TOPICS if missing(name)]
    todo_token = missing("VKWH_TOKEN_ID")
    if not todo_topics and not todo_token:
        print("Everything is already set up in .env. Nothing to do.")
        return

    print(f"Operator {h.operator_id()} on testnet. Creating what's missing...\n")
    lines, links = [], []
    try:
        for env_name, memo in todo_topics:
            topic_id = h.create_topic(memo)
            lines.append(f"{env_name}={topic_id}")
            links.append(h.hashscan_topic(topic_id))
        if todo_token:
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
