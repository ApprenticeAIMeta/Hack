"""The judge's path through a fresh clone: pages, the bundled procedure and run, no API key needed."""

import pytest
from fastapi.testclient import TestClient

from apprentice import server


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:
        yield c


def test_pages_load(client):
    for path in ("/", "/guide"):
        r = client.get(path)
        assert r.status_code == 200 and "<html" in r.text.lower()


def test_procedure_in_english_and_hindi(client):
    en = client.get("/api/procedure/airplane").json()
    hi = client.get("/api/procedure/airplane?lang=hi").json()
    assert len(en["steps"]) == len(hi["steps"]) == 7
    assert en["steps"][-1].get("final_action") == "throw"
    assert hi["steps"][0]["title"] != en["steps"][0]["title"]


def test_bundled_run_is_listed(client):
    names = [s["session"] for s in client.get("/api/sessions").json()]
    assert "catch5" in names


def test_mirror_frame(client):
    r = client.get("/api/mirror/airplane/40.0.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize("session,name", [("..", "0000001.wav"), ("catch5", "../meta.json"), ("catch5", "x.txt")])
def test_recorded_audio_rejects_paths_outside_a_session(client, session, name):
    assert client.get(f"/api/recorded/{session}/asks/{name}").status_code in (400, 404)
