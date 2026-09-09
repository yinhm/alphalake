"""Parse Damodaran beta spreadsheets (betas.xls, betaGlobal.xls, betaChina.xls).

Data on sheet "Industry Averages", header at row 9.
"""

from __future__ import annotations

from pathlib import Path

if __package__:
    from .xls_utils import (
        open_data_sheet, find_header_row, get_headers, build_col_map, safe_float, safe_int,
    )
else:
    from xls_utils import (
        open_data_sheet, find_header_row, get_headers, build_col_map, safe_float, safe_int,
    )


def parse_betas(file_path: str | Path) -> dict[str, dict]:
    """Parse betas .xls → dict keyed by industry name."""
    ws = open_data_sheet(str(file_path), "Industry Averages")
    header_row = find_header_row(ws, "Industry Name")
    col = build_col_map(get_headers(ws, header_row))

    result: dict[str, dict] = {}
    for r in range(header_row + 1, ws.nrows):
        name = (safe_str_cell(ws, r, col.get("Industry Name")) or "").strip()
        if not name or name.lower() == "total market":
            continue
        result[name] = {
            "beta_u": safe_float(ws, r, col.get("Unlevered beta")),
            "beta_u_corrected_for_cash": safe_float(ws, r, col.get("Unlevered beta corrected for cash")),
            "d_e_ratio": safe_float(ws, r, col.get("D/E Ratio")),
            "effective_tax_rate": safe_float(ws, r, col.get("Effective Tax rate")),
            "number_of_firms": safe_int(ws, r, col.get("Number of firms")),
        }
    return result


def _find_col(col_map: dict[str, int], candidates: list[str]) -> int | None:
    """Find the first matching column from a list of candidate header names."""
    for c in candidates:
        if c in col_map:
            return col_map[c]
    # Fallback: partial match (header contains candidate)
    for c in candidates:
        for h, idx in col_map.items():
            if c.lower() in h.lower():
                return idx
    return None


def safe_str_cell(ws, row, col):
    if col is None:
        return None
    try:
        v = ws.cell_value(row, col)
    except IndexError:
        return None
    return str(v).strip() if v != "" else None


def alphalake_beta_snapshot(file_path: str | Path) -> dict:
    """全量 94 行全球行业快照；保留来源的去杠杆税率选择。"""
    import hashlib
    import math
    import platform
    from decimal import Decimal, ROUND_HALF_EVEN
    import xlrd

    path = Path(file_path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("oversized beta workbook")
    wb = xlrd.open_workbook(path)
    ws = wb.sheet_by_name("Industry Averages")  # 禁止旧工具的猜测工作表回退。
    parsed = parse_betas(path)
    expected = ["Industry Name", "Number of firms", "Beta", "D/E Ratio", "Effective Tax rate",
                "Unlevered beta", "Cash/Firm value", "Unlevered beta corrected for cash"]
    if [str(v).strip() for v in ws.row_values(9)[:8]] != expected or ws.cell_value(2, 5) != "Global":
        raise ValueError("unsupported global beta headers/region")
    if ws.cell_value(0, 0) != "Date updated:" or ws.cell_type(0, 1) != xlrd.XL_CELL_DATE:
        raise ValueError("missing beta date")
    if ws.cell_value(7, 5) != "Marginal":
        raise ValueError("unsupported unlevering tax choice")
    tax = ws.cell_value(8, 5)
    if not isinstance(tax, (int, float)) or not math.isfinite(tax) or not 0 <= tax <= 1:
        raise ValueError("invalid marginal tax")
    def decimal(v):
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError("missing/non-numeric beta value")
        return str(Decimal(str(v)).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN))
    if ws.nrows != 106 or ws.cell_value(104, 0) != "Total Market" or ws.cell_value(105, 0) != "Total Market (without financials)":
        raise ValueError("unsupported/incomplete industry scope")
    rows, names = [], set()
    for r in range(10, 104):
        name = ws.cell_value(r, 0)
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("missing/duplicate industry")
        names.add(name)
        n = ws.cell_value(r, 1)
        if not isinstance(n, (int, float)) or not math.isfinite(n) or n <= 0 or n != int(n):
            raise ValueError("invalid industry sample count")
        # 源公式闭合仅是代数核验；不声称独立市场估计。
        levered, de, unlevered, cash, corrected = [ws.cell_value(r, c) for c in (2, 3, 5, 6, 7)]
        for v in (levered, de, unlevered, cash, corrected): decimal(v)
        if abs(unlevered - levered / (1 + (1 - tax) * de)) > 1e-10 or abs(corrected - unlevered / (1 - cash)) > 1e-10:
            raise ValueError("beta formula mismatch")
        for metric, c, key in (("beta_unlevered", 5, "beta_u"),
                               ("beta_unlevered_cash_adjusted", 7, "beta_u_corrected_for_cash"),
                               ("debt_equity_ratio", 3, "d_e_ratio"),
                               ("effective_tax_rate", 4, "effective_tax_rate")):
            v = parsed[name][key]
            if v != ws.cell_value(r, c): raise ValueError("beta parser disagreement")
            rows.append(dict(industry=name, sample_count=int(n), metric_code=metric,
                             source_locator=f"Industry Averages!{chr(65+c)}{r+1}", raw_value=str(v), value=decimal(v)))
    date = xlrd.xldate_as_datetime(ws.cell_value(0, 1), wb.datemode).date().isoformat()
    wb.release_resources()
    return dict(contract="alphalake-global-beta-v1", observation_date=date,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(), parser_version="damodaran-global-beta-v1",
                runtime=f"python={platform.python_version()};xlrd={xlrd.__version__}",
                unlevering_tax_rate=decimal(tax), observations=rows)


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description="AlphaLake global industry beta snapshot")
    parser.add_argument("workbook", type=Path)
    print(json.dumps(alphalake_beta_snapshot(parser.parse_args().workbook), sort_keys=True, allow_nan=False))
