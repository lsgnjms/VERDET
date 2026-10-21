"""Deployment and PWA pieces; four-step verification UI. Run: pytest tests/test_deploy.py"""

import json
from pathlib import Path

from sqlmodel import Session

from app.models import Meter, Reading, database_url

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def test_database_url_names_the_postgres_driver():
    assert database_url("postgres://u:p@host/db") == "postgresql+psycopg://u:p@host/db"
    assert database_url("postgresql://u:p@ep-x.neon.tech/db?sslmode=require") == \
        "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require"
    assert database_url("sqlite:///./verdet.db") == "sqlite:///./verdet.db"


def test_database_url_forgives_copy_paste_extras():
    expected = "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require"
    for pasted in [
        "  postgresql://u:p@ep-x.neon.tech/db?sslmode=require \n",
        "'postgresql://u:p@ep-x.neon.tech/db?sslmode=require'",
        "psql 'postgresql://u:p@ep-x.neon.tech/db?sslmode=require'",
    ]:
        assert database_url(pasted) == expected


def test_bad_database_url_gives_a_clear_message_without_the_secret():
    import pytest
    from app.models import make_engine
    with pytest.raises(RuntimeError, match="isn't a valid database URL") as err:
        make_engine("not a url with secret-password")
    assert "secret-password" not in str(err.value)


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200 and resp.json() == {"ok": True}


def test_service_worker_is_served_from_the_root(client):
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/javascript")
    assert "caches.match(\"/offline\")" in resp.text


def test_manifest_is_installable(client):
    resp = client.get("/static/manifest.json")
    assert resp.status_code == 200
    manifest = json.loads(resp.text)
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/dashboard"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200


def test_offline_page_needs_no_cdn(client):
    html = client.get("/offline").text
    assert "You're offline" in html and "cdn." not in html


def test_pages_link_the_manifest_and_register_the_worker(client):
    html = client.get("/").text
    assert 'rel="manifest"' in html and 'serviceWorker.register("/sw.js")' in html


def test_verification_shows_four_steps_and_no_ai(client, engine):
    client.post("/signup", data={"email": "ada@example.com", "password": "correct horse"})
    with Session(engine) as session:
        session.add(Meter(meter_id="VD-0001", owner_id=1, pubkey_hex="a" * 64, rated_w=10, lat=6.52, lon=3.38))
        session.add(Reading(meter_id="VD-0001", seq=1, t_start_ms=0, t_end_ms=60000, mwh=150, peak_mw=9000,
                            avg_mv=18000, avg_ma=500, hash="x", decision="accepted"))
        session.commit()
    table = client.get("/readings/table").text
    assert table.count('aria-label="verification steps"') == 1
    assert 'title="AI' not in table and "→ AI" not in table
    assert "AI_ANOMALY" not in client.get("/").text
