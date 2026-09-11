"""Parse Damodaran fundamental growth spreadsheets (fundgrEB.xls, etc.).

Expected Growth in EBIT保留为经营利润增长参考，不冒充收入增长。
"""
from __future__ import annotations

from pathlib import Path

from .xls_utils import (
    open_data_sheet, find_header_row, get_headers, build_col_map, safe_float, safe_int, safe_str,
)


def parse_fundgr(file_path: str | Path) -> dict[str, dict]:
    """Parse fundamental growth .xls → dict keyed by industry name."""
    ws = open_data_sheet(str(file_path), "Industry Averages")
    header_row = find_header_row(ws, "Industry Name")
    col = build_col_map(get_headers(ws, header_row))

    if "Expected Growth in EBIT" not in col:
        raise ValueError("missing Expected Growth in EBIT header")

    result: dict[str, dict] = {}
    for r in range(header_row + 1, ws.nrows):
        name = (safe_str(ws, r, col.get("Industry Name")) or "").strip()
        if not name or name.lower() in ("total market", "total"):
            continue
        result[name] = {
            "expected_ebit_growth": safe_float(ws, r, col.get("Expected Growth in EBIT")),
            "roc": safe_float(ws, r, col.get("ROC")),
            "reinvestment_rate": safe_float(ws, r, col.get("Reinvestment Rate")),
        }
    return result
