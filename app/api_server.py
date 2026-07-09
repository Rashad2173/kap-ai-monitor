from __future__ import annotations

from dotenv import load_dotenv
load_dotenv(override=True)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.schemas.api import AskRequest, AskResponse, DashboardSummaryResponse
from app.services.dashboard_service import get_dashboard_summary
from app.services.question_router import route_question


app = FastAPI(
    title="KAP AI Monitor API",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "message": "KAP AI Monitor API çalışıyor.",
        "openai_model": settings.OPENAI_MODEL,
        "openai_key_loaded": bool(settings.OPENAI_API_KEY),
        "chroma_path": settings.CHROMA_PATH,
        "chroma_collection": settings.CHROMA_COLLECTION_NAME,
    }


@app.get("/api/dashboard/summary", response_model=DashboardSummaryResponse)
def dashboard_summary(bank: str = "ALL"):
    return get_dashboard_summary(bank=bank)


@app.post("/api/ask", response_model=AskResponse)
def ask(request: AskRequest):
    return route_question(request)