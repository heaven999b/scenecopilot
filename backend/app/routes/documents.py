from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from ..agent.tools import docs as docs_tool
from ..config import DEMO_USER_ID, UPLOADS_DIR
from ..db import conn_ctx
from ..models import DocumentSearchResponse, DocumentUploadResponse
from ..storage import copy_upload_to_path

router = APIRouter(prefix="/api/documents", tags=["documents"])


def _extract_text(upload: UploadFile, source_path: Path) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if upload.content_type and upload.content_type.startswith("text/"):
        return source_path.read_text(encoding="utf-8", errors="ignore")
    if suffix in {".txt", ".md", ".json"}:
        return source_path.read_text(encoding="utf-8", errors="ignore")
    return (
        "Binary document uploaded successfully. Add a parser or OCR engine here "
        "to extract text from PDFs, images, or office files in production."
    )


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    tags: str | None = Form(default=None),
) -> DocumentUploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"
    dest = UPLOADS_DIR / safe_name
    await copy_upload_to_path(file, dest)
    text = _extract_text(file, dest)
    doc_title = title or Path(file.filename).stem
    parsed_tags = [tag.strip() for tag in (tags or "").split(",") if tag.strip()]
    summary = text.splitlines()[0].strip() if text.strip() else doc_title

    with conn_ctx() as conn:
        cur = conn.execute(
            """
            INSERT INTO documents (user_id, title, filename, source_path, text_content, summary, tags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                DEMO_USER_ID,
                doc_title,
                file.filename,
                str(dest.resolve()),
                text,
                summary,
                json.dumps(parsed_tags),
            ),
        )
        document_id = cur.lastrowid

    docs_tool.index_document_sync(
        document_id=document_id,
        user_id=DEMO_USER_ID,
        title=doc_title,
        text=text,
    )
    docs_tool.bump_documents_version()
    return DocumentUploadResponse(document_id=document_id, title=doc_title)


@router.get("/search", response_model=DocumentSearchResponse)
async def search_documents(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=5, ge=1, le=20),
    include_external: bool = Query(default=False),
) -> DocumentSearchResponse:
    result = await docs_tool.search_documents(
        query=q,
        limit=limit,
        user_id=DEMO_USER_ID,
        include_external=include_external,
    )
    return DocumentSearchResponse(query=q, items=result["items"])
