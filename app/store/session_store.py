"""In-memory, process-local session store for in-flight intent-capture
conversations. Same single-worker caveat as PlanStore."""

from __future__ import annotations

from app.models.session import SessionState


class SessionNotFoundError(KeyError):
    pass


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def create(self) -> SessionState:
        session = SessionState()
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> SessionState:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise SessionNotFoundError(session_id) from None

    def save(self, session: SessionState) -> SessionState:
        self._sessions[session.id] = session
        return session
