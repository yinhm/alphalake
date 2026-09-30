from tools.audit_data_gaps import source_requests, summarize


def test_gap_probe_uses_standard_fields_and_cumulative_terms():
    coverage = dict(companies=[dict(code='300866', scope='nonfinancial_by_reference', gaps=[
        dict(category='research_ttm',field='r_and_d_expense',period='2026-06-30',series='ttm'),
        dict(category='research_history',field='r_and_d_expense',period='2025-12-31',series='annual'),
        dict(category='market_capital_inputs',field='mv_equity_listing',period='2026-06-30',series='company')]),
        dict(code='601375',scope='outside_financial_scope',gaps=[dict(field='ebit')])])
    assert source_requests(coverage)==[dict(code='300866',period=p,field='research_and_development_expense')
                                     for p in ('2025-06-30','2025-12-31','2026-06-30')]


def test_scope_conflicts_and_missing_reference_keep_denominators():
    coverage = dict(report_period='2026-06-30',information_as_of='2026-09-30T10:00:00Z',
        snapshot_candidates=3,full_source_universe=False,scope_counts={'unresolved_industry_scope':3},
        companies=[dict(scope='unresolved_industry_scope',industry_evidence=members) for members in (
            [], [dict(source='tdx',node_code='X5101',node_name='证券'),dict(source='damodaran',node_name='Retail (General)')],
            [dict(source='damodaran',node_name='Retail (General)')])])
    result = summarize(coverage,{'results':[]})
    assert result['classification']==dict(missing_exact_reference_classification=1,
        source_classification_conflict=1,ambiguous_or_unverified_reference_membership=1)
    assert result['snapshot_candidates']==3 and not result['full_source_universe']
