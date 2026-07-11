import pytest

from app.store.session_store import SessionNotFoundError, SessionStore


def test_create_and_get_round_trip():
    store = SessionStore()
    session = store.create()
    assert store.get(session.id) is session


def test_save_persists_mutations():
    store = SessionStore()
    session = store.create()
    session.constraints_complete = True
    store.save(session)
    assert store.get(session.id).constraints_complete is True


def test_get_missing_session_raises():
    store = SessionStore()
    with pytest.raises(SessionNotFoundError):
        store.get("does-not-exist")
