"""
backend/tests/test_persistence.py

Unit tests for the file-based incident persistence layer
(backend/persistence.py) that Block 3's state-persistence work added.
Uses a temp directory (monkeypatched over the module's PERSIST_DIR) so
tests never touch the real /tmp/sre_incidents used by a running backend.
"""
from __future__ import annotations

from backend import persistence


def test_save_and_load_incident_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "PERSIST_DIR", tmp_path)
    state = {"incident_id": "inc_test1", "status": "RESOLVED", "alerts": []}
    persistence.save_incident("inc_test1", state)

    loaded = persistence.load_incident("inc_test1")
    assert loaded == state


def test_load_incident_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "PERSIST_DIR", tmp_path)
    assert persistence.load_incident("inc_nonexistent") is None


def test_load_all_incidents_returns_every_saved_file(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "PERSIST_DIR", tmp_path)
    persistence.save_incident("inc_a", {"incident_id": "inc_a"})
    persistence.save_incident("inc_b", {"incident_id": "inc_b"})

    all_incidents = persistence.load_all_incidents()
    assert set(all_incidents.keys()) == {"inc_a", "inc_b"}
    assert all_incidents["inc_a"]["incident_id"] == "inc_a"


def test_load_all_incidents_empty_dir_returns_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "PERSIST_DIR", tmp_path)
    assert persistence.load_all_incidents() == {}


def test_save_incident_does_not_raise_on_unwritable_dir(monkeypatch):
    # PERSIST_DIR pointed at a path that can't exist as a directory (its
    # parent is a file) -- save_incident must swallow the I/O error rather
    # than crash the caller, matching backend/store.py's "never raises"
    # assumption for _persist().
    import pathlib

    monkeypatch.setattr(persistence, "PERSIST_DIR", pathlib.Path("/dev/null/not_a_dir"))
    persistence.save_incident("inc_x", {"incident_id": "inc_x"})  # must not raise
