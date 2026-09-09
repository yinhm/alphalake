"""Parse Damodaran country risk premium spreadsheet (ctryprem.xlsx).

Data on sheet "ERPs by country", header at row 8, data from row 9.
Columns: Country, Region, Moody's rating, Default Spread, Total ERP, Country Risk Premium
"""

from __future__ import annotations

from pathlib import Path

import openpyxl


def parse_country_risk(file_path: str | Path) -> dict[str, dict]:
    """Parse ctryprem.xlsx → dict keyed by country name."""
    wb = openpyxl.load_workbook(str(file_path), data_only=True)
    ws = wb["ERPs by country"]

    # Find header row by looking for "Country" in column A
    header_row = None
    for r in range(1, 15):
        val = ws.cell(r, 1).value
        if val and str(val).strip() == "Country":
            header_row = r
            break
    if header_row is None:
        raise ValueError("Could not find 'Country' header in ERPs by country sheet")

    # Build column map (1-indexed)
    headers = {}
    for c in range(1, ws.max_column + 1):
        val = ws.cell(header_row, c).value
        if val:
            headers[str(val).strip()] = c

    result: dict[str, dict] = {}
    for r in range(header_row + 1, ws.max_row + 1):
        country = ws.cell(r, headers["Country"]).value
        if not country or not str(country).strip():
            continue
        country = str(country).strip()

        result[country] = {
            "region": _cell_str(ws, r, headers.get("Africa")),  # Column B is region, labeled "Africa" in header
            "moodys_rating": _cell_str(ws, r, headers.get("Moody's rating")),
            "default_spread": _cell_float(ws, r, headers.get("Rating-based Default Spread")),
            "equity_risk_premium": _cell_float(ws, r, headers.get("Total Equity Risk Premium")),
            "country_risk_premium": _cell_float(ws, r, headers.get("Country Risk Premium")),
        }

    # Also extract the mature market ERP from the metadata rows
    mature_erp = None
    for r in range(1, header_row):
        val = ws.cell(r, 1).value
        if val and "mature equity market" in str(val).lower():
            mature_erp = _cell_float(ws, r, 5)  # Column E typically
            break

    if mature_erp is not None:
        result["__mature_market_erp__"] = {"equity_risk_premium": mature_erp}

    wb.close()
    return result


def _cell_float(ws, row: int, col: int | None) -> float | None:
    if col is None:
        return None
    val = ws.cell(row, col).value
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _cell_str(ws, row: int, col: int | None) -> str | None:
    if col is None:
        return None
    val = ws.cell(row, col).value
    return str(val).strip() if val else None


def alphalake_country_snapshot(file_path: str | Path) -> dict:
    """严格的首批 CN/HK/US 评级法快照；不推断公告时间或 CDS 口径。"""
    import hashlib
    import math
    import platform
    from decimal import Decimal, ROUND_HALF_EVEN

    path = Path(file_path)
    import zipfile
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist()) > 4096 or sum(i.file_size for i in archive.infolist()) > 128 * 1024 * 1024:
            raise ValueError("oversized country-risk workbook")
    parsed = parse_country_risk(path)  # 复用已有数值解析，严格边界在此补齐。
    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        ws = wb["ERPs by country"]
        expected = {"A8": "Country", "D8": "Rating-based Default Spread",
                    "E8": "Total Equity Risk Premium", "F8": "Country Risk Premium"}
        if any(ws[cell].value != label for cell, label in expected.items()):
            raise ValueError("unsupported country-risk headers")
        if ws["A2"].value != "Date of update:" or not hasattr(ws["B2"].value, "date"):
            raise ValueError("missing observation date")
        if ws["A3"].value != "Enter the current risk premium for a mature equity market":
            raise ValueError("unsupported mature ERP label")
        date = ws["B2"].value.date().isoformat()
        observations = []

        def add(kind, code, metric, cell, value):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"missing/non-numeric required value: {cell}")
            if value != ws[cell].value or not 0 <= value <= 1:
                raise ValueError(f"invalid country risk value: {cell}")
            raw = str(value)  # openpyxl 数值表示；原始 Excel 字节完整归档。
            observations.append(dict(subject_kind=kind, subject_code=code, metric_code=metric,
                                     source_locator=f"ERPs by country!{cell}", raw_value=raw,
                                     value=str(Decimal(raw).quantize(Decimal('0.000000000001'), rounding=ROUND_HALF_EVEN))))

        add("market_group", "mature", "mature_market_erp", "E3",
            parsed["__mature_market_erp__"]["equity_risk_premium"])
        for name, code in (("China", "CN"), ("Hong Kong", "HK"), ("United States", "US")):
            rows = [r for r in range(9, ws.max_row + 1) if ws.cell(r, 1).value == name]
            if len(rows) != 1:
                raise ValueError(f"missing/duplicate country: {name}")
            r = rows[0]
            for metric, column, key in (("sovereign_default_spread", "D", "default_spread"),
                                        ("total_equity_risk_premium", "E", "equity_risk_premium"),
                                        ("country_risk_premium", "F", "country_risk_premium")):
                add("country", code, metric, f"{column}{r}", parsed[name][key])
        # 仅验证原文明确成立的 CN/HK 加总，美国行保留源定义，不套同一公式。
        mature = Decimal(observations[0]["value"])
        for code in ("CN", "HK"):
            values = {o["metric_code"]: Decimal(o["value"]) for o in observations if o["subject_code"] == code}
            if abs(values["total_equity_risk_premium"] - values["country_risk_premium"] - mature) > Decimal('0.000000000002'):
                raise ValueError(f"ERP components inconsistent: {code}")
        return dict(contract="alphalake-country-risk-v1", observation_date=date,
                    workbook_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    parser_version="damodaran-country-selected-v1",
                    runtime=f"python={platform.python_version()};openpyxl={openpyxl.__version__}",
                    observations=observations)
    finally:
        wb.close()


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description="AlphaLake CN/HK/US country risk snapshot")
    parser.add_argument("workbook", type=Path)
    args = parser.parse_args()
    print(json.dumps(alphalake_country_snapshot(args.workbook), allow_nan=False, sort_keys=True))
