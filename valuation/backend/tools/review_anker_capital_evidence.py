"""复验安克既有资本分量，判定公司倍率证据是否足够；不拟合或改估值。"""
import csv
import hashlib
import json
from pathlib import Path
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[3]


def verify_components():
    import csv, gzip, hashlib, json, struct
    from copy import deepcopy
    from decimal import Decimal as D, ROUND_HALF_UP
    from pathlib import Path
    p=ROOT/'valuation/research/continuing-operations-five'
    source_path=p/'capital-snapshot.json'
    ledger_path=ROOT/'internal/ingest/testdata/anker-history-2026/reported.csv'
    source=json.loads(source_path.read_bytes());ledger=list(csv.DictReader(ledger_path.open()))
    fields={'FN114':('cash_capex',1),'FN304':('rd_expense',1),'FN136':('cf_fixed_da',1),'FN137':('cf_intangible_da',1),'FN138':('cf_deferred_da',1),'FN146':('cf_inventory',1),'FN147':('cf_receivables',1),'FN148':('cf_payables',1),'FN579':('cf_property_da',10000),'FN581':('cf_rou_da',10000)}
    bits=lambda n:struct.unpack('<I',struct.pack('<f',float(n)))[0]
    value=lambda n:D(str(struct.unpack('<f',struct.pack('<I',n))[0]))
    def build(s):
     index={(r['code'],r['period']):r for r in s['records']}
     assert len(index)==len(s['records'])
     rows=[];comparisons=[]
     for year in range(2021,2026):
      r=index['300866',f'{year}-12-31'];amounts={};conflicts=[]
      for field,(key,multiplier) in fields.items():
       pdf,=[x for x in ledger if x['year']==str(year) and x['key']==key]
       encoded=D(pdf['value'])/multiplier
       if multiplier==10000:encoded=encoded.quantize(D('.01'),rounding=ROUND_HALF_UP)
       matched=r['bits'][field]==bits(encoded)
       comparisons.append(dict(year=year,field=field,source_bits=r['bits'][field],source_value=str(value(r['bits'][field])),value_multiplier=multiplier,pdf_row_id=pdf['id'],pdf_id=pdf['pdf_id'],pdf_page=int(pdf['pdf_page']),pdf_column=int(pdf['column']),pdf_decimal=pdf['value'],expected_source_bits=bits(encoded),status='source_precision_match' if matched else 'source_pdf_version_conflict_unresolved'))
       amounts[field]=value(r['bits'][field])*multiplier
       if not matched:conflicts.append(field)
      da=sum((amounts[f] for f in ('FN136','FN137','FN138','FN579')),D(0))
      wc=-sum((amounts[f] for f in ('FN146','FN147','FN148')),D(0))
      subtotal=amounts['FN114']-da+wc
      rows.append(dict(year=year,cash_capex_cny=str(amounts['FN114']),matched_nonlease_da_cny=str(da),cashflow_wc_cash_use_cny=str(wc),rou_depreciation_separate_cny=str(amounts['FN581']),rd_expense_separate_cny=str(amounts['FN304']),source_formula_subtotal_cny=str(subtotal),evidence_supported_subtotal_cny=None if conflicts else str(subtotal),conflict_fields=conflicts,classified_reinvestment=None,actual_fcff=None,status='source_pdf_conflict' if conflicts else 'matched_components_not_full_reinvestment'))
     conflicts=[(c['year'],c['field']) for c in comparisons if c['status']!='source_precision_match']
     assert conflicts==[(2022,'FN148')],conflicts
     return dict(code='300866',years=list(range(2021,2026)),comparison_count=50,matched_count=49,conflict_count=1,comparisons=comparisons,annual_components=rows,boundary='source_precision_preserved; later_acquired_and_restated_versions; source_subtotal_is_not_full_net_reinvestment_or_FCFF; excluded_lease_investment_RD_reclassification_disposal_and_other_adjustments_not_zero; no_capital_ratio_adoption_or_forecast_validation')
    r=build(source)
    for field in ('FN136','FN581'):
     changed=deepcopy(source)
     row=next(x for x in changed['records'] if x['code']=='300866' and x['period']=='2024-12-31');row['bits'][field]^=1
     try:build(changed)
     except AssertionError:pass
     else:raise AssertionError('source tamper accepted')
    r['evidence']={str(x.relative_to(ROOT)):hashlib.sha256(x.read_bytes()).hexdigest() for x in (source_path,ledger_path,ROOT/'internal/ingest/testdata/anker-history-2026/annual-inputs.csv')}
    r['verification']=['one_yuan_field_bit_tamper_rejected','one_wanyuan_field_bit_tamper_rejected','known_2022_conflict_retained_not_overwritten']
    data=(json.dumps(r,ensure_ascii=False,indent=2)+'\n').encode();target=p/'anker-capital-components.json.gz'
    assert gzip.decompress(target.read_bytes())==data
    return r


def review():
    historical = verify_components()
    history = ROOT/'internal/ingest/testdata/anker-history-2026'
    with (history/'rd-cohorts.csv').open() as file:
        cohorts = list(csv.DictReader(file))
    assert [int(r['vintage']) for r in cohorts] == list(range(2020, 2026))
    opening = closing = amortization = Decimal(0)
    for row in cohorts:
        year = int(row['vintage'])
        expense = Decimal(row['rd_expense'])
        age = 2025-year
        before = expense*Decimal(max(6-age, 0))/5 if age else Decimal(0)
        after = expense*Decimal(max(5-age, 0))/5
        amort = expense/5 if age else Decimal(0)
        assert before == Decimal(row['opening_asset'])
        assert after == Decimal(row['closing_asset'])
        assert amort == Decimal(row['amortization'])
        opening += before
        closing += after
        amortization += amort
    adjustment = Decimal(cohorts[-1]['rd_expense'])-amortization
    assert closing-opening == adjustment
    wc_path = ROOT/'internal/ingest/testdata/anker-valuation-2026/working-capital.csv'
    with wc_path.open() as file:
        wc = list(csv.DictReader(file))
    missing = [r for r in wc if r['item'] == 'change_in_fully_classified_operating_wc']
    assert {r['period'] for r in missing} == {'FY-2025', 'TTM-2026-06-30'}
    assert all(r['value'] == '' and r['status'] == 'missing_historical_classification' for r in missing)
    assert all(r['classified_reinvestment'] is None and r['actual_fcff'] is None for r in historical['annual_components'])
    return dict(code='300866', historical_years=list(range(2021, 2026)),
        historical_source_matches=historical['matched_count'], source_conflicts=historical['conflict_count'],
        rd_scenario=dict(period='FY-2025', source='existing_pdf_decimal_research_not_standard_fact',
            assumed_life_years=5, opening_asset_cny=str(opening), closing_asset_cny=str(closing),
            amortization_cny=str(amortization), earnings_and_reinvestment_adjustment_cny=str(adjustment),
            fcff_change_from_reclassification_cny='0',
            boundary='same tax and same cash flows; increase both NOPAT and reinvestment, not an added cash outflow or growth forecast'),
        classified_wc_gaps=[dict(period=r['period'], reason=r['formula']) for r in missing],
        company_marginal_sales_to_capital=None,
        decision='retain_explicit_industry_proxy_no_company_ratio_adoption',
        reasons=['full_operating_working_capital_not_classified',
                 'lease_additions_disposals_and_noncash_investment_not_reconciled',
                 'rd_reclassification_not_proof_of_incremental_sales_productivity',
                 'historical_source_research_not_complete_standard_company_capital_history'],
        stopping_rule='do_not_fit_ratio_to_incomplete_subtotals_or_scan_growth_thresholds; reopen when classified capital bridge is supplied',
        evidence={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (history/'rd-cohorts.csv', wc_path)},
        baseline_policy_unchanged=True)


if __name__ == '__main__':
    print(json.dumps(review(), ensure_ascii=False, indent=2))
