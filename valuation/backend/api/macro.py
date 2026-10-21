"""只读宏观页面API，消费已发布文件。"""
import hashlib

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from data_sources.macro_pages import MacroPageError, catalog, encoded, read_page

router = APIRouter(prefix='/macro', tags=['macro'])


def respond(request, value):
    tag = '"' + hashlib.sha256(encoded(value)).hexdigest() + '"'
    headers = {'ETag': tag, 'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'}
    if request.headers.get('if-none-match') == tag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(value, headers=headers)


def detail(request, identifier):
    try:
        return respond(request, read_page(identifier).model_dump(mode='json'))
    except MacroPageError as exc:
        raise HTTPException(exc.status, detail={'reason': exc.reason}) from exc


@router.get('')
def index(request: Request):
    try:
        return respond(request, catalog())
    except MacroPageError as exc:
        raise HTTPException(exc.status, detail={'reason': exc.reason}) from exc


@router.get('/latest')
def latest(request: Request):
    try:
        identifier = catalog()['current_id']
    except MacroPageError as exc:
        raise HTTPException(exc.status, detail={'reason': exc.reason}) from exc
    if identifier is None:
        raise HTTPException(404, detail={'reason': '尚未发布宏观阅读页'})
    return detail(request, identifier)


@router.get('/publications/{identifier}')
def publication(identifier: str, request: Request):
    return detail(request, identifier)
