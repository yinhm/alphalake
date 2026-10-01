from types import SimpleNamespace

import pytest
from tools import audit_industry_candidates as tool


def test_regional_comparison_keeps_missing_values_and_rejects_mislabelled_source(monkeypatch, tmp_path):
    labels = {'betas.xls':'US companies', 'betaGlobal.xls':'Global',
        'betaChina.xls':'China', 'betaemerg.xls':'Emerging Markets'}
    headers = ['Industry Name','Number of firms','Beta ','D/E Ratio','Effective Tax rate',
        'Unlevered beta','Cash/Firm value','Unlevered beta corrected for cash']
    for name in labels:
        (tmp_path/name).write_bytes(name.encode())
    def workbook(path):
        label = labels[path.name]
        cells = {(2,5):label,(0,0):'Date updated:',(0,1):46027,
            (7,5):'Marginal',(8,5):0.25,(10,0):'Steel'}
        sheet = SimpleNamespace(nrows=11, cell_value=lambda r,c:cells[r,c],
            cell_type=lambda r,c:tool.xlrd.XL_CELL_DATE, row_values=lambda r:headers)
        return SimpleNamespace(sheet_by_name=lambda _:sheet, datemode=0, release_resources=lambda:None)
    monkeypatch.setattr(tool.xlrd,'open_workbook',workbook)
    monkeypatch.setattr(tool,'parse_betas',lambda _:dict(Steel=dict(beta_u=1,
        beta_u_corrected_for_cash=1.1, d_e_ratio=0.2, effective_tax_rate=0.2, number_of_firms=8)))
    result = tool.regional_betas(tmp_path, {'Steel','Not disclosed'})
    assert len(result)==4 and result['China']['industries']['Not disclosed'] is None
    assert result['US']['industries']['Steel']['source_locator']=='Industry Averages!A11:H11'
    assert result['US']['sha256']!=result['Global']['sha256']
    assert result['Emerging']['observation_date']=='2026-01-05'
    labels['betaChina.xls']='Global'
    with pytest.raises(ValueError, match='region/headers/date'):
        tool.regional_betas(tmp_path, {'Steel'})
