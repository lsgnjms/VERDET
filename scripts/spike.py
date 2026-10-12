"""Phase 0 spike: prove every Hedera operation VERDET needs, using app/hedera.py.

Creates a throwaway topic and token (separate from the real ones made by
setup_hedera.py), two test users, and one atomic VKWH<->HBAR swap.
Costs the operator about 20 HBAR (10 per test user) plus fees. Testnet only.

Run: .venv\\Scripts\\python.exe scripts\\spike.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so "app" imports

from app import hedera as h  # noqa: E402

SWAP_MWH = 500_000            # 0.5 VKWH
SWAP_TINYBAR = 2 * 100_000_000  # 2 HBAR


def step(label: str, url: str) -> None:
    print(f"  {label:<26} {url}")


def wait_for_balances(account_id: str, token_id: str, expected_mwh: int) -> dict | None:
    """The mirror node lags consensus by a few seconds; retry until the
    VKWH balance shows the swap (otherwise we may print a pre-swap snapshot)."""
    for _ in range(10):
        balances = h.get_balances(account_id, token_id)
        if balances and balances["vkwh_mwh"] == expected_mwh:
            return balances
        time.sleep(3)
    return h.get_balances(account_id, token_id)


def main() -> None:
    print(f"Operator {h.operator_id()} on testnet\n")

    topic = h.create_topic("VERDET spike")
    step("topic", h.hashscan_topic(topic))
    msg_tx = h.submit_message(topic, '{"t":"spike","v":1,"hello":"VERDET"}')
    step("message tx", h.hashscan_tx(msg_tx))

    token = h.create_vkwh_token()
    step("token (spike VKWH)", h.hashscan_token(token))
    mint_tx = h.mint_vkwh(token, 1_000_000)  # 1 VKWH
    step("mint 1 VKWH tx", h.hashscan_tx(mint_tx))

    seller, seller_key = h.create_user_account()
    step("seller account", h.hashscan_account(seller))
    buyer, buyer_key = h.create_user_account()
    step("buyer account", h.hashscan_account(buyer))

    reward_tx = h.reward_vkwh(token, seller, 1_000_000)
    step("treasury -> seller 1 VKWH", h.hashscan_tx(reward_tx))

    swap_tx = h.atomic_swap(token, seller, seller_key, buyer, buyer_key, SWAP_MWH, SWAP_TINYBAR)
    step("ATOMIC SWAP tx", h.hashscan_tx(swap_tx))

    print("\nBalances from the mirror node (expected: seller 0.5 VKWH, ~12 HBAR; buyer 0.5 VKWH, ~8 HBAR):")
    for name, account in (("seller", seller), ("buyer", buyer)):
        b = wait_for_balances(account, token, SWAP_MWH)
        if b is None:
            print(f"  {name}: mirror node not ready yet, check HashScan")
        else:
            print(f"  {name}: {b['vkwh_mwh'] / 1e6:.6f} VKWH, {b['hbar_tinybar'] / 1e8:.4f} HBAR")

    print("\nCheckpoint: open the ATOMIC SWAP link. It must be ONE transaction with a token transfer and an HBAR transfer.")


if __name__ == "__main__":
    try:
        main()
    except h.HederaError as exc:
        sys.exit(f"Spike failed: {exc}")
