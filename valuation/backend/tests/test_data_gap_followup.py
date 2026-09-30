from tools.audit_data_gaps import source_requests, summarize, target_gap_causes


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
    result = summarize(coverage,{'results':[], 'standard_chain':[dict(code='300866',period='2025-12-31',field='revenue_cumulative',status=s) for s in ('available_standard_fact','rejected_source_zero','no_source_record')]})
    assert result['classification']==dict(missing_exact_reference_classification=1,
        source_classification_conflict=1,ambiguous_or_unverified_reference_membership=1)
    assert result['snapshot_candidates']==3 and not result['full_source_universe']

    assert result['standard_chain_cells']==dict(available_standard_fact=1,rejected_source_zero=1,no_source_record=1)


def test_ttm_missing_period_and_zero_rejection_are_not_missing_parsed_facts():
    coverage=dict(companies=[dict(code='300866',instrument_id=7,scope='nonfinancial_by_reference',gaps=[
        dict(category='operating_inputs',field='revenues',series='ttm',period='2026-06-30')])])
    probe=dict(standard_chain=[dict(code='300866',field='revenue_cumulative',period=p,status=s,source_record_id=i,instrument_id=7)
        for i,(p,s) in enumerate([('2025-12-31','available_standard_fact'),('2026-06-30','rejected_source_zero'),
                                  ('2025-06-30','no_source_record')])])
    result=target_gap_causes(coverage,probe)[0]
    assert result['available_components']==1
    assert result['causes']==['no_source_record','rejected_source_zero']
    assert {r['period'] for r in result['missing_components']}=={'2026-06-30','2025-06-30'}
    probe['standard_chain'].append(probe['standard_chain'][0])
    assert 'ambiguous_standard_source_identity' in target_gap_causes(coverage,probe)[0]['causes']

    probe['standard_chain'].pop()
    probe['standard_chain'][0]['instrument_id']=8
    assert 'different_security_identity' in target_gap_causes(coverage,probe)[0]['causes']
