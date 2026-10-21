"""FastAPI application entry point — serves API + bundled frontend."""

from pathlib import Path
import math

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from engine.module_4_dcf import InvalidTerminalValue, InvalidReinvestment, InvalidResearchTax, InvalidTaxForecast
from engine.equity_bridge import InvalidConvertibleInput
from engine.data_dictionary import InvalidValuationInput

from .alphalake import router as alphalake_router
from .routes import router
from .export import router as export_router
from .admin import router as admin_router
from .database import router as database_router, valuation_router as db_valuation_router
from .knowledge import router as knowledge_router
from .research import router as research_router
from .macro import router as macro_router
from .valuation_results import router as valuation_results_router
from .frontend import mount_frontend

app = FastAPI(title="Valuation Engine API", version="0.1.0")


@app.exception_handler(RequestValidationError)
async def invalid_request_input(request: Request, error: RequestValidationError):
    # Invalid NaN/Infinity inputs must not break the JSON rejection response.
    detail = jsonable_encoder(error.errors(), custom_encoder={
        float: lambda value: value if math.isfinite(value) else str(value)})
    return JSONResponse(status_code=422, content={'detail': detail})


@app.exception_handler(InvalidTerminalValue)
@app.exception_handler(InvalidReinvestment)
@app.exception_handler(InvalidResearchTax)
@app.exception_handler(InvalidTaxForecast)
@app.exception_handler(InvalidConvertibleInput)
@app.exception_handler(InvalidValuationInput)
async def invalid_dcf_input(request: Request, error: ValueError):
    return JSONResponse(status_code=422, content={'detail': {'status': 'rejected_input_or_policy', 'reason': str(error)}})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)

app.include_router(alphalake_router)
app.include_router(router)
app.include_router(export_router)
# Admin + DB endpoints live under /api/admin and /api/database; mount at /api/*.
app.include_router(admin_router, prefix="/api")
app.include_router(database_router, prefix="/api")
app.include_router(db_valuation_router, prefix="/api")
app.include_router(knowledge_router, prefix="/api")
app.include_router(research_router, prefix="/api")
app.include_router(macro_router, prefix="/api")
app.include_router(valuation_results_router, prefix="/api")

DIST_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@app.get("/health")
def health():
    return {"status": "ok"}


mount_frontend(app, DIST_DIR)
