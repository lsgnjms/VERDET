"""Phase 3 step 2: every decision goes to HCS; accepted energy mints VKWH to the owner.
Run: pytest tests/test_anchoring.py
"""

import json

from sqlmodel import Session, select

from app.models import Reading
from tests.test_readings_api import meter  # noqa: F401  (pytest fixture)
from tests.test_verify import make_reading, make_signed

READINGS_TOPIC = "0.0.1002"
VKWH_TOKEN = "0.0.1004"
OWNER = "0.0.999001"


def post(client, reading: dict) -> dict:
    resp = client.post("/api/readings", json=reading)
    assert resp.status_code == 200, resp.text
    return resp.json()


def hcs_messages(fake_hedera) -> list[dict]:
    """The decisions posted to the readings topic, decoded."""
    return [json.loads(m) for topic, m in fake_hedera.messages if topic == READINGS_TOPIC]


def stored(engine) -> list[Reading]:
    with Session(engine) as session:
        return list(session.exec(select(Reading).order_by(Reading.id)))


def test_accepted_reading_mints_rewards_and_anchors(client, engine, fake_hedera, meter):
    body = post(client, make_reading(seq=0))
    assert body["decision"] == "accepted"

    assert fake_hedera.mints == [(VKWH_TOKEN, 167)]              # 167 mWh = 0.000167 VKWH
    assert fake_hedera.rewards == [(VKWH_TOKEN, OWNER, 167)]     # to the meter's owner
    [msg] = hcs_messages(fake_hedera)
    assert msg == {
        "t": "reading", "v": 1, "meterId": "VD-0001", "seq": 0, "mWh": 167, "peakMw": 10120,
        "hash": body["hash"], "decision": "accepted", "reasons": [],
        "mintTx": body["mintTx"], "rewardTx": body["rewardTx"],
    }
    [row] = stored(engine)
    assert row.mint_tx == body["mintTx"] and row.hcs_tx == body["hcsTx"]
    assert body["mintTx"] and body["rewardTx"] and body["hcsTx"]
    assert "hederaError" not in body


def test_rejected_readings_are_anchored_without_reward(client, engine, fake_hedera, meter):
    post(client, make_reading(seq=1))                              # accepted
    post(client, make_reading(seq=1))                              # DUPLICATE
    post(client, make_reading(seq=0))                              # REPLAY
    post(client, make_reading(seq=2, mWh=99999))                   # BAD_SIGNATURE
    post(client, make_signed(seq=3, peakMw=30000, mWh=450))        # PHYSICS_LIMIT
    post(client, make_reading(meterId="VD-9999"))                  # UNREGISTERED

    msgs = hcs_messages(fake_hedera)
    assert [m["reasons"] for m in msgs] == [
        [], ["DUPLICATE"], ["REPLAY"], ["BAD_SIGNATURE"], ["PHYSICS_LIMIT"], ["UNREGISTERED"],
    ]
    assert all("mintTx" not in m for m in msgs[1:])                # no reward fields on rejections
    assert len(fake_hedera.mints) == 1                             # only the accepted one minted
    assert all(r.hcs_tx for r in stored(engine))                   # every decision anchored


def test_every_hcs_message_is_under_1kb(client, fake_hedera, meter):
    post(client, make_reading(seq=1))
    post(client, make_reading(meterId="X" * 32))
    assert all(len(m.encode()) < 1024 for _, m in fake_hedera.messages)


def test_zero_energy_reading_is_anchored_but_not_minted(client, fake_hedera, meter):
    body = post(client, make_signed(seq=1, mWh=0, peakMw=0, avgMv=0, avgMa=0))
    assert body["decision"] == "accepted"
    assert fake_hedera.mints == [] and fake_hedera.rewards == []
    [msg] = hcs_messages(fake_hedera)
    assert msg["mintTx"] is None and msg["rewardTx"] is None


def test_mint_failure_keeps_decision_and_still_anchors(client, engine, fake_hedera, meter):
    fake_hedera.fail_mint = True
    body = post(client, make_reading(seq=1))
    assert body["decision"] == "accepted"                          # the decision stands
    assert body["mintTx"] is None and "Reward not sent" in body["hederaError"]
    [msg] = hcs_messages(fake_hedera)
    assert msg["decision"] == "accepted" and msg["mintTx"] is None
    [row] = stored(engine)
    assert row.mint_tx is None and row.hcs_tx                      # "reward pending"


def test_hcs_failure_keeps_decision(client, engine, fake_hedera, meter):
    fake_hedera.fail_hcs = True
    body = post(client, make_reading(seq=1))
    assert body["decision"] == "accepted"
    assert body["hcsTx"] is None and "not recorded on Hedera" in body["hederaError"]
    [row] = stored(engine)
    assert row.decision == "accepted" and row.hcs_tx is None       # "not anchored"
    assert row.mint_tx                                             # the reward still went out


def test_owner_total_equals_sum_of_accepted_mwh(client, engine, fake_hedera, meter):
    """The Phase 3 checkpoint, in miniature."""
    for seq in range(10):
        post(client, make_signed(seq=seq, mWh=150 + seq, peakMw=10000))
    post(client, make_signed(seq=10, peakMw=30000, mWh=450))       # spike: rejected
    accepted = sum(r.mwh for r in stored(engine) if r.decision == "accepted")
    rewarded = sum(amount for _, to, amount in fake_hedera.rewards if to == OWNER)
    assert rewarded == accepted == sum(150 + s for s in range(10))
    assert len(hcs_messages(fake_hedera)) == 11


def test_malformed_reading_is_not_anchored(client, fake_hedera, meter):
    resp = client.post("/api/readings", json=make_reading(seq=1, mWh=1.5))
    assert resp.status_code == 422
    assert fake_hedera.messages == [] and fake_hedera.mints == []
