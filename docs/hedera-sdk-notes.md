# Hedera SDK notes (Hiero Python SDK)

Method names VERDET uses, checked against the `hiero-ledger/hiero-sdk-python` repo (`examples/` and `src/`) on 2026-10-12. All of these are called only from `app/hedera.py`. Testnet only.

## The basic pattern

Every transaction follows the same steps:

```python
receipt = (
    SomeTransaction()
    .set_...(...)            # fill in the details
    .freeze_with(client)     # lock the transaction so it can be signed
    .sign(key)               # add a signature (call again for more keys)
    .execute(client)         # send it and wait for the receipt
)
if receipt.status != ResponseCode.SUCCESS:
    raise RuntimeError(f"Hedera error: {ResponseCode(receipt.status).name}")
```

Imports come from the package root, e.g. `from hiero_sdk_python import Client, TransferTransaction, ResponseCode`.

## Client

```python
client = Client.from_env()          # reads the operator account + key and NETWORK from .env
operator_id = client.operator_account_id
operator_key = client.operator_private_key
```

Set `NETWORK=testnet` in `.env`.

## HCS topics (decision log)

```python
# Create (one-time, in scripts/setup_hedera.py)
receipt = (
    TopicCreateTransaction(memo="VERDET readings", admin_key=operator_key.public_key())
    .freeze_with(client).sign(operator_key).execute(client)
)
topic_id = receipt.topic_id

# Submit a reading decision (keep under 1 KB)
receipt = (
    TopicMessageSubmitTransaction(topic_id=topic_id, message=message)
    .freeze_with(client).sign(operator_key).execute(client)
)
tx_id = receipt.transaction_id
```

The submit receipt doesn't give the topic sequence number in the example. If we need it, read it from the mirror node.

## VKWH token

```python
# Create (one-time). 6 decimals: 1 unit = 1 mWh, 1 VKWH = 1 kWh.
receipt = (
    TokenCreateTransaction()
    .set_token_name("VERDET kWh")
    .set_token_symbol("VKWH")
    .set_decimals(6)
    .set_initial_supply(0)
    .set_treasury_account_id(operator_id)
    .set_token_type(TokenType.FUNGIBLE_COMMON)
    .set_supply_type(SupplyType.INFINITE)
    .set_admin_key(admin_key)
    .set_supply_key(supply_key)
    .freeze_with(client)
    .sign(operator_key).sign(admin_key).sign(supply_key)
    .execute(client)
)
token_id = receipt.token_id

# Mint for an accepted reading (amount in mWh units). Must be signed by the supply key.
receipt = (
    TokenMintTransaction()
    .set_token_id(token_id)
    .set_amount(mwh)
    .freeze_with(client).sign(supply_key).execute(client)
)
```

Minted tokens land in the treasury. Moving them to the meter owner is a normal token transfer (below).

## Custodial user accounts

```python
user_key = PrivateKey.generate_ed25519()
receipt = (
    AccountCreateTransaction()
    .set_key_without_alias(user_key.public_key())
    .set_initial_balance(Hbar(1))
    .freeze_with(client).sign(operator_key).execute(client)
)
account_id = receipt.account_id

# The account must associate VKWH before it can hold it. Signed by the user's key.
receipt = (
    TokenAssociateTransaction()
    .set_account_id(account_id)
    .add_token_id(token_id)
    .freeze_with(client).sign(user_key).execute(client)
)
```

The user's private key is encrypted (with `cryptography`) before it is stored. Never log it.

## Marketplace buy: one atomic transaction (rule 9)

Confirmed in the SDK source:
- `TransferTransaction` builds the HBAR legs and the token legs into one `CryptoTransferTransactionBody`, so they succeed or fail together.
- `.sign()` can be called several times. Each signature is kept, so seller and buyer can both sign.

```python
tx = (
    TransferTransaction()
    .add_token_transfer(vkwh_id, seller_id, -amount_mwh)    # VKWH leg
    .add_token_transfer(vkwh_id, buyer_id, amount_mwh)
    .add_hbar_transfer(buyer_id, -price_tinybar)             # HBAR leg
    .add_hbar_transfer(seller_id, price_tinybar)
    .freeze_with(client)
    .sign(seller_key)
    .sign(buyer_key)
)
receipt = tx.execute(client)
```

Each leg must add up to zero (what leaves one account arrives in the other).

## Watch out

- **HBAR units:** `add_hbar_transfer(account_id, amount)` accepts `int | Hbar`. A plain `int` means **tinybars** (1 HBAR = 100,000,000 tinybars). `Hbar(1)` means 1 HBAR. Use `Hbar.from_tinybars(n)` to convert.
- **Fee payer:** the operator account pays transaction fees unless set otherwise.
- **Not yet checked:** the exact signature of `add_token_transfer`. It lives in a base class (`AbstractTokenTransferTransaction`). The examples call it as `(token_id, account_id, amount)`. Confirm this when writing `app/hedera.py`.

## Where to look

- Examples: https://github.com/hiero-ledger/hiero-sdk-python/tree/main/examples (`consensus/`, `tokens/`, `transaction/`)
- Docs: https://docs.hedera.com (also available as the `hedera-docs` MCP server)
- HashScan testnet: `https://hashscan.io/testnet/transaction/{txId}`
