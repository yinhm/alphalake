"""不依赖估值会话的只读词条 API。"""

import hashlib
import json

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse

from knowledge.store import KnowledgeError, database_path, read_index, read_term, search_terms

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def cached_response(request: Request, payload: dict) -> Response:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    etag = '"' + hashlib.sha256(body).hexdigest() + '"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    candidates = [value.strip().removeprefix("W/") for value in request.headers.get("if-none-match", "").split(",")]
    if etag in candidates or "*" in candidates:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def api_error(error: KnowledgeError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail={"code": error.code, "message": error.message})


@router.get("/assets/{digest}.png")
def knowledge_image(request: Request, digest: str = Path(pattern=r"^[0-9a-f]{64}$")):
    directory = database_path().parent / "assets"
    path = directory / f"{digest}.png"
    # 内容寻址只允许本目录的普通文件，不能沿软链接读取其他文件。
    if path.is_symlink() or not path.is_file():
        raise HTTPException(status_code=404, detail="知识库图片不存在")
    try:
        if directory.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("invalid asset")
        body = path.read_bytes()
        if not body.startswith(b"\x89PNG\r\n\x1a\n") or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("invalid asset")
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="知识库图片校验失败") from exc
    etag = f'"{digest}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"}
    candidates = [value.strip().removeprefix("W/") for value in request.headers.get("if-none-match", "").split(",")]
    if etag in candidates or "*" in candidates:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="image/png", headers=headers)


@router.get("/index")
def knowledge_index(request: Request):
    try:
        return cached_response(request, read_index())
    except KnowledgeError as exc:
        raise api_error(exc) from exc


@router.get("/terms/{term_id}")
def knowledge_term(request: Request, term_id: str, release_id: str | None = Query(default=None, max_length=128)):
    try:
        return cached_response(request, read_term(term_id, release_id))
    except KnowledgeError as exc:
        raise api_error(exc) from exc


@router.get("/search")
def knowledge_search(q: str = Query(min_length=1, max_length=160), limit: int = Query(default=20, ge=1, le=50),
                     release_id: str | None = Query(default=None, max_length=128)):
    try:
        return JSONResponse(content=search_terms(q, limit, release_id), headers={"Cache-Control": "no-cache"})
    except KnowledgeError as exc:
        raise api_error(exc) from exc
