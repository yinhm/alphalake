"""只读研究目录、正文和显式登记的附件。"""
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from data_sources.research_reports import (MEDIA, ResearchError, catalog, read_report,
                                           report_detail, verified_file)

router = APIRouter(prefix='/research', tags=['research'])


def failure(error):
    return HTTPException(error.status_code, detail=dict(code=error.code, reason=str(error)))


@router.get('')
def list_research(q: str = Query(default='', max_length=160),
                  kind: Literal['initial_coverage', 'thesis', 'independent_dcf', 'narrative_batch', 'research_note'] | None = None,
                  offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100)):
    try:
        return catalog(q, kind, offset, limit)
    except ResearchError as exc:
        raise failure(exc) from exc


@router.get('/{identifier}')
def get_research(identifier: str, document: str | None = Query(default=None, max_length=80)):
    try:
        return report_detail(identifier, document)
    except ResearchError as exc:
        raise failure(exc) from exc


@router.get('/{identifier}/files/{file_id}')
def research_file(identifier: str, file_id: str, download: bool = False):
    try:
        item, path = verified_file(read_report(identifier), file_id)
    except ResearchError as exc:
        raise failure(exc) from exc
    suffix = PurePosixPath(item.path).suffix.lower()
    inline = not download and suffix in {'.pdf', '.png', '.jpg', '.jpeg', '.webp'}
    return FileResponse(path, media_type=MEDIA[suffix], filename=path.name,
                        content_disposition_type='inline' if inline else 'attachment',
                        headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-cache',
                                 'Content-Security-Policy': "sandbox; default-src 'none'"})
