"""All Hedera calls for VERDET live here (CLAUDE.md rule 3). Testnet only.

Amounts are integers: VKWH in mWh units (6 decimals, 1 unit = 1 mWh),
HBAR in tinybars (1 HBAR = 100,000,000 tinybars).
"""

import logging
import os
from functools import lru_cache

import httpx
from dotenv import load_dotenv
from hiero_sdk_python import (
    AccountCreateTransaction,
    AccountId,
    Client,
    Hbar,
    Network,
    PrivateKey,
    ResponseCode,
    SupplyType,
    TokenCreateTransaction,
    TokenId,
    TokenMintTransaction,
    TokenType,
    TopicCreateTransaction,
    TopicId,
    TopicMessageSubmitTransaction,
    TransferTransaction,
)

load_dotenv()
log = logging.getLogger(__name__)

STARTING_HBAR = 10          # every new user gets this much from the operator
MAX_HCS_BYTES = 1024        # rule 6: HCS messages stay under 1 KB
HASHSCAN = "https://hashscan.io/testnet"


class HederaError(Exception):
    """A Hedera call failed. The message is safe to show in the UI."""


# --- Client and keys -------------------------------------------------------

def _load_operator_key() -> PrivateKey:
    """Parse OPERATOR_KEY with the loader that matches its type and format.

    No auto-detection: the SDK's generic loader reads a raw 32-byte key as
    Ed25519, which would silently break an ECDSA portal key.
    """
    key_str = os.getenv("OPERATOR_KEY", "").strip()
    key_type = os.getenv("OPERATOR_KEY_TYPE", "").strip().lower()
    key_format = os.getenv("OPERATOR_KEY_FORMAT", "").strip().lower()
    if not key_str:
        raise HederaError("OPERATOR_KEY is missing from .env.")
    try:
        if key_format == "der":
            key = PrivateKey.from_string_der(key_str)
        elif key_format == "hex" and key_type == "ecdsa":
            key = PrivateKey.from_string_ecdsa(key_str)
        elif key_format == "hex" and key_type == "ed25519":
            key = PrivateKey.from_string_ed25519(key_str)
        else:
            raise HederaError(
                "Set OPERATOR_KEY_TYPE (ecdsa/ed25519) and OPERATOR_KEY_FORMAT (hex/der) in .env."
            )
    except ValueError:
        # The SDK's error text contains the key itself, so don't pass it on.
        raise HederaError(
            f"OPERATOR_KEY could not be read as {key_type} {key_format}. Check .env."
        ) from None
    # A DER key carries its own type; make sure it matches what .env says.
    if key_type == "ecdsa" and not key.is_ecdsa():
        raise HederaError("OPERATOR_KEY is not an ECDSA key, but OPERATOR_KEY_TYPE=ecdsa.")
    if key_type == "ed25519" and not key.is_ed25519():
        raise HederaError("OPERATOR_KEY is not an Ed25519 key, but OPERATOR_KEY_TYPE=ed25519.")
    return key


@lru_cache(maxsize=1)
def get_client() -> Client:
    """Build the testnet client once and reuse it. Refuses any other network."""
    if os.getenv("NETWORK", "").strip().lower() != "testnet":
        raise HederaError("NETWORK must be 'testnet' in .env. VERDET never runs on mainnet.")
    operator_id = os.getenv("OPERATOR_ID", "").strip()
    if not operator_id:
        raise HederaError("OPERATOR_ID is missing from .env.")
    client = Client(Network("testnet"))
    client.set_operator(AccountId.from_string(operator_id), _load_operator_key())
    return client


def operator_id() -> AccountId:
    return get_client().operator_account_id


def operator_key() -> PrivateKey:
    return get_client().operator_private_key


def key_to_string(key: PrivateKey) -> str:
    """DER hex: it records the key type, so it reloads without guessing."""
    return key.to_string_der()


def key_from_string(der_hex: str) -> PrivateKey:
    try:
        return PrivateKey.from_string_der(der_hex)
    except ValueError:
        raise HederaError("A stored account key could not be read.") from None


# --- Shared helpers ---------------------------------------------------------

def _tx_str(tx_id) -> str:
    """Format a TransactionId as '0.0.x@seconds.nanos' (nanos zero-padded)."""
    start = tx_id.valid_start
    return f"{tx_id.account_id}@{start.seconds}.{start.nanos:09d}"


def _run(tx, *signers: PrivateKey):
    """Freeze, sign, execute and check one transaction. Returns the receipt.

    The operator pays the fee and the SDK adds its signature automatically.
    """
    name = type(tx).__name__
    try:
        tx.freeze_with(get_client())
        for key in signers:
            tx.sign(key)
        receipt = tx.execute(get_client())
    except HederaError:
        raise
    except Exception as exc:
        log.warning("%s failed: %s", name, type(exc).__name__)
        raise HederaError(f"Hedera is slow or unreachable, try again. ({type(exc).__name__})") from exc
    if receipt.status != ResponseCode.SUCCESS:
        status = ResponseCode(receipt.status).name
        log.warning("%s returned %s", name, status)
        raise HederaError(f"Hedera rejected the transaction: {status}")
    return receipt


# --- HCS topics -------------------------------------------------------------

def config_id(env_name: str) -> str:
    """A topic or token ID from .env, e.g. config_id("READINGS_TOPIC_ID")."""
    value = os.getenv(env_name, "").strip()
    if not value:
        raise HederaError(f"{env_name} is missing from .env. Run scripts/setup_hedera.py first.")
    return value


def create_topic(memo: str) -> str:
    """Create a topic only the operator can post to. Returns the topic ID."""
    key = operator_key()
    tx = (
        TopicCreateTransaction()
        .set_memo(memo)
        .set_admin_key(key.public_key())
        .set_submit_key(key.public_key())
    )
    return str(_run(tx, key).topic_id)


def submit_message(topic_id: str, message: str) -> str:
    """Post a message (under 1 KB) to a topic. Returns the transaction ID."""
    if len(message.encode("utf-8")) >= MAX_HCS_BYTES:
        raise HederaError("HCS message is 1 KB or larger; make it smaller.")
    tx = TopicMessageSubmitTransaction(topic_id=TopicId.from_string(topic_id), message=message)
    return _tx_str(_run(tx, operator_key()).transaction_id)


# --- VKWH token -------------------------------------------------------------

def create_vkwh_token() -> str:
    """Create VKWH: 6 decimals, 0 initial supply, treasury/admin/supply = operator."""
    key = operator_key()
    tx = (
        TokenCreateTransaction()
        .set_token_name("VERDET kWh")
        .set_token_symbol("VKWH")
        .set_decimals(6)
        .set_initial_supply(0)
        .set_treasury_account_id(operator_id())
        .set_token_type(TokenType.FUNGIBLE_COMMON)
        .set_supply_type(SupplyType.INFINITE)
        .set_admin_key(key.public_key())
        .set_supply_key(key.public_key())
    )
    return str(_run(tx, key).token_id)


def mint_vkwh(token_id: str, amount_mwh: int) -> str:
    """Mint VKWH into the treasury. Returns the transaction ID."""
    if amount_mwh <= 0:
        raise HederaError("Mint amount must be positive.")
    tx = TokenMintTransaction().set_token_id(TokenId.from_string(token_id)).set_amount(amount_mwh)
    return _tx_str(_run(tx, operator_key()).transaction_id)


def reward_vkwh(token_id: str, to_account: str, amount_mwh: int) -> str:
    """Move freshly minted VKWH from the treasury to a meter owner."""
    token = TokenId.from_string(token_id)
    tx = (
        TransferTransaction()
        .add_token_transfer(token, operator_id(), -amount_mwh)
        .add_token_transfer(token, AccountId.from_string(to_account), amount_mwh)
    )
    return _tx_str(_run(tx, operator_key()).transaction_id)


# --- Custodial user accounts ------------------------------------------------

def create_user_account() -> tuple[str, str]:
    """Create a user's testnet account with STARTING_HBAR and one automatic
    token association (for VKWH). Returns (account_id, key as DER hex).
    The caller must encrypt the key before storing it.
    """
    user_key = PrivateKey.generate_ed25519()
    tx = (
        AccountCreateTransaction()
        .set_key_without_alias(user_key.public_key())
        .set_initial_balance(Hbar(STARTING_HBAR))
        .set_max_automatic_token_associations(1)
    )
    receipt = _run(tx)
    return str(receipt.account_id), key_to_string(user_key)


# --- Marketplace ------------------------------------------------------------

def atomic_swap(
    token_id: str,
    seller_id: str,
    seller_key: str,
    buyer_id: str,
    buyer_key: str,
    amount_mwh: int,
    price_tinybar: int,
) -> str:
    """One TransferTransaction: VKWH seller->buyer and HBAR buyer->seller.
    Both legs succeed or fail together (rule 9). Keys are DER hex strings.
    """
    token = TokenId.from_string(token_id)
    seller = AccountId.from_string(seller_id)
    buyer = AccountId.from_string(buyer_id)
    tx = (
        TransferTransaction()
        .add_token_transfer(token, seller, -amount_mwh)
        .add_token_transfer(token, buyer, amount_mwh)
        .add_hbar_transfer(buyer, -price_tinybar)
        .add_hbar_transfer(seller, price_tinybar)
    )
    receipt = _run(tx, key_from_string(seller_key), key_from_string(buyer_key))
    return _tx_str(receipt.transaction_id)


# --- Mirror node ------------------------------------------------------------

def get_balances(account_id: str, token_id: str | None = None) -> dict | None:
    """Read HBAR (tinybars) and VKWH (mWh) from the mirror node.
    Returns None if the mirror node is slow or doesn't know the account yet.
    """
    base = os.getenv("MIRROR_URL", "https://testnet.mirrornode.hedera.com/api/v1").rstrip("/")
    token_id = token_id or os.getenv("VKWH_TOKEN_ID", "")
    try:
        resp = httpx.get(f"{base}/accounts/{account_id}", timeout=5)
        resp.raise_for_status()
        balance = resp.json()["balance"]
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    vkwh = next((t["balance"] for t in balance.get("tokens", []) if t["token_id"] == token_id), 0)
    return {"hbar_tinybar": balance["balance"], "vkwh_mwh": vkwh}


# --- HashScan links ---------------------------------------------------------

def hashscan_tx(tx_id: str) -> str:
    """'0.0.x@secs.nanos' -> HashScan URL (which uses '0.0.x-secs-nanos')."""
    account, start = tx_id.split("@")
    secs, nanos = start.split(".")
    return f"{HASHSCAN}/transaction/{account}-{secs}-{nanos}"


def hashscan_account(account_id: str) -> str:
    return f"{HASHSCAN}/account/{account_id}"


def hashscan_topic(topic_id: str) -> str:
    return f"{HASHSCAN}/topic/{topic_id}"


def hashscan_token(token_id: str) -> str:
    return f"{HASHSCAN}/token/{token_id}"
