from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.constraints import Constraints
from app.models.plan import Plan
from app.models.session import ChatMessage
from app.services.trip_service import TripService, get_trip_service
from app.store.session_store import SessionNotFoundError

router = APIRouter(prefix="/sessions", tags=["sessions"])


class StartSessionResponse(BaseModel):
    session_id: str


class SessionView(BaseModel):
    session_id: str
    messages: list[ChatMessage]
    constraints: Constraints
    constraints_complete: bool


class SendMessageRequest(BaseModel):
    message: str


class SendMessageResponse(BaseModel):
    assistant_reply: str
    constraints: Constraints
    constraints_complete: bool


@router.post("", response_model=StartSessionResponse)
def start_session(service: TripService = Depends(get_trip_service)) -> StartSessionResponse:
    session = service.start_session()
    return StartSessionResponse(session_id=session.id)


@router.get("/{session_id}", response_model=SessionView)
def get_session(session_id: str, service: TripService = Depends(get_trip_service)) -> SessionView:
    try:
        session = service.session_store.get(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="session not found") from None
    return SessionView(
        session_id=session.id,
        messages=session.messages,
        constraints=session.constraints,
        constraints_complete=session.constraints_complete,
    )


@router.post("/{session_id}/messages", response_model=SendMessageResponse)
async def send_message(
    session_id: str, body: SendMessageRequest, service: TripService = Depends(get_trip_service)
) -> SendMessageResponse:
    try:
        session = await service.send_message(session_id, body.message)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="session not found") from None
    return SendMessageResponse(
        assistant_reply=session.messages[-1].content,
        constraints=session.constraints,
        constraints_complete=session.constraints_complete,
    )


@router.post("/{session_id}/compose", response_model=Plan)
async def compose(session_id: str, service: TripService = Depends(get_trip_service)) -> Plan:
    try:
        return await service.compose(session_id)
    except SessionNotFoundError:
        raise HTTPException(status_code=404, detail="session not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
