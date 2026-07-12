import gradio as gr
from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.plans import router as plans_router
from app.api.sessions import router as sessions_router
from app.ui.gradio_app import demo as gradio_demo

app = FastAPI(title="Trip Architect")
app.include_router(sessions_router)
app.include_router(plans_router)
app.include_router(admin_router)

gr.mount_gradio_app(app, gradio_demo, path="/")
