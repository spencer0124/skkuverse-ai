from fastapi import FastAPI

from app.routes import chat, health, notices

app = FastAPI(title="skkuverse-ai", version="1.0.0")

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(notices.router)
