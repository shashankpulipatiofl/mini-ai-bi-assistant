"""
FastAPI application.

Endpoints:
  GET  /               -> simple web page (static/index.html)
  GET  /api/health     -> status + whether the LLM is configured
  POST /api/ask        -> {"question": "..."}  ->  structured answer
  GET  /api/data-quality -> rows rejected while loading the CSV

Run:  uvicorn app.main:app --reload
"""

import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()  # reads GROQ_API_KEY from a .env file if present

from app import database, llm, qa_service  # noqa: E402  (after load_dotenv)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app):
    # Re-build the database from the CSV every time the server starts.
    summary = database.load_csv_to_db()
    logger.info("Loaded %s rows, rejected %s", summary["loaded"], len(summary["rejected"]))
    yield


app = FastAPI(title="Mini AI BI Assistant", lifespan=lifespan)


class QuestionRequest(BaseModel):
    question: str


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "llm_enabled": llm.is_llm_enabled()}


@app.get("/api/data-quality")
def data_quality():
    columns, rows = database.get_rejected_records()
    return {"rejected_count": len(rows), "columns": columns, "rows": rows}


@app.post("/api/ask")
def ask(request: QuestionRequest):
    try:
        return qa_service.answer_question(request.question)
    except qa_service.InvalidQuestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except qa_service.UnsupportedQuestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except qa_service.NoDataError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except llm.LLMError as exc:
        logger.error("LLM error: %s", exc)
        raise HTTPException(status_code=503, detail=f"AI service unavailable: {exc}")
    except sqlite3.Error as exc:
        logger.exception("Database error")
        raise HTTPException(status_code=500, detail=f"Database error: {exc}")
