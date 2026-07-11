from fastapi import FastAPI

from app.api.plans import router as plans_router
from app.api.sessions import router as sessions_router

app = FastAPI(title="Trip Architect")
app.include_router(sessions_router)
app.include_router(plans_router)
