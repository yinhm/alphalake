"""已冻结估值结果的只读选股查询。"""
import hashlib
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response

from data_sources.research_reports import ResearchError
from data_sources.valuation_results import ResultQuery, datasets, query_results

router = APIRouter(prefix='/valuation-results', tags=['valuation-results'])


@router.get('/datasets')
def result_datasets():
    try:
        return datasets()
    except ResearchError as exc:
        raise HTTPException(exc.status_code, detail=dict(code=exc.code, reason=str(exc))) from exc


@router.get('/{dataset_id}')
def result_rows(dataset_id: str, request: Request, response: Response,
                query: Annotated[ResultQuery, Query()]):
    try:
        body = query_results(dataset_id, query)
    except ResearchError as exc:
        raise HTTPException(exc.status_code, detail=dict(code=exc.code, reason=str(exc))) from exc
    tag = '"' + hashlib.sha256((body['dataset']['content_hash'] + query.model_dump_json()).encode()).hexdigest() + '"'
    headers = {'ETag': tag, 'Cache-Control': 'no-cache', 'X-Content-Version': body['dataset']['content_hash']}
    if request.headers.get('if-none-match') == tag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return body
