"""兼容快照：单位、累计差分、缺项与污染拒绝。"""
from datetime import date, datetime
import json
import sqlite3
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from tools import export_alphalake_sqlite as exporter


class SQLiteExportTest(unittest.TestCase):
    def test_reported_earnings_use_standard_cumulative_facts(self):
        for column, field in [('ebit', 'reported_ebit'), ('ebitda', 'reported_ebitda')]:
            facts = {}
            for period, basis, value in [('2026-03-31', 'Q1', '1000000'), ('2026-06-30', 'H1', '3000000')]:
                facts[(period, field)] = dict(instrument_id=7, unit='CNY', period_type=basis,
                    statement_scope='provider_default', value=value, fact_id=period,
                    available_at='2026-08-31T00:00:00Z', artifact_sha256='source-evidence')
            value, status, evidence = exporter.cell(facts, set(), 7, date(2026, 6, 30), column, False)
            self.assertEqual((value, status), (2.0, 'available'))
            self.assertEqual([row['coefficient'] for row in evidence], [1, -1])
            del facts[('2026-03-31', field)]
            self.assertEqual(exporter.cell(facts, set(), 7, date(2026, 6, 30), column, False)[:2],
                             (None, 'missing_standard_fact'))

    def test_snapshot_and_rejections(self):
        period = date(2026, 6, 30)
        asof = datetime.fromisoformat('2026-09-22T00:00:00+00:00')
        company = dict(symbols=['sz300866'], symbol_count=1, identifier_count=1,
                       exchange_mic='XSHE', instrument_id=7, name='测试公司')
        facts = []
        for end, value in [('2025-09-30', '7000000'), ('2025-12-31', '10000000'),
                           ('2026-03-31', '0'), ('2026-06-30', '4000000')]:
            facts.append(dict(source='tdx', code='300866', instrument_id=7, period=end,
                              field='revenue_cumulative', canonical_field='revenue_cumulative',
                              value=value, unit='CNY', period_type={3:'Q1', 6:'H1', 9:'9M', 12:'FY'}[int(end[5:7])],
                              statement_scope='provider_default', fact_id=end,
                              available_at='2026-08-31T00:00:00Z', artifact_sha256='evidence'))
        facts.append(dict(facts[-1], field='profit_before_tax', canonical_field='profit_before_tax', value='2000000'))
        facts.append(dict(facts[-1], field='total_shares', canonical_field='total_shares',
                          value='500000000', unit='share', period_type='instant'))

        for field, value in [('income_tax_expense', '200000'), ('profit_before_tax', '1000000')]:
            facts.append(dict(facts[1], field=field, canonical_field=field, value=value))

        facts.append(dict(facts[1], field='other_noncurrent_financial_assets',
                          canonical_field='other_noncurrent_financial_assets', period_type='instant', value='-1'))

        def fetch(code, end):
            return dict(contract_version='alphalake-valuation-v2', code=code,
                        report_period=end.isoformat(), information_as_of=asof.isoformat(),
                        windows=[], supplements=[{'item': 'not_exported'}], facts=[r for r in facts if end.year-1 <= int(r['period'][:4]) and r['period'] <= end.isoformat()],
                        source_conflicts=[])

        with sqlite3.connect(':memory:') as db:
            db.row_factory = sqlite3.Row
            self.assertEqual(exporter.export_snapshot(db, [company], fetch, period, asof), 1)
            self.assertEqual(db.execute('SELECT count(*) FROM standard_facts').fetchone()[0], len(facts))
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='valuation_inputs'").fetchone()[0], 0)
            data = exporter.target.fetch_company(db, 'SZSE:300866')
            self.assertEqual(len(data['financials_annual']), 10)
            self.assertEqual(len(data['financials_quarterly']), 8)
            self.assertEqual(data['financials_annual'][0]['revenues'], 10)
            self.assertIsNone(data['financials_annual'][0]['cross_holdings'])
            self.assertEqual(db.execute("SELECT value FROM standard_facts WHERE field='other_noncurrent_financial_assets'").fetchone()[0], '-1')
            self.assertEqual(db.execute("SELECT status FROM export_cells WHERE series='annual' AND period_offset=0 AND field='cross_holdings'").fetchone()[0], 'negative_investment_component_requires_review')
            self.assertEqual(data['company']['effective_tax_rate'], 0.2)
            self.assertEqual([r['revenues'] for r in data['financials_quarterly'][:4]], [4, 0, 3, None])
            self.assertEqual(data['financials_quarterly'][0]['shares_outstanding'], 500)
            self.assertIsNone(data['financials_quarterly'][0]['ebit'])
            self.assertIsNone(data['financials_quarterly'][0]['earnings_before_tax'])
            self.assertEqual(db.execute("SELECT status FROM export_cells WHERE field='earnings_before_tax' LIMIT 1").fetchone()[0], 'requires_separate_valuation_definition')
            self.assertIsNone(data['financials_annual'][1]['revenues'])
            evidence = json.loads(db.execute("SELECT evidence_json FROM export_cells WHERE series='quarterly' AND period_offset=0 AND field='revenues'").fetchone()[0])
            self.assertEqual([r['coefficient'] for r in evidence], [1, -1])
        indexed = {(r['period'], r['field']): r for r in facts}
        self.assertEqual(exporter.cell(indexed, {'2026-03-31'}, 7, period, 'revenues', False)[1], 'source_record_conflict')
        facts[0]['unit'] = 'USD'
        with self.assertRaisesRegex(ValueError, 'unit'):
            exporter.cell(indexed, set(), 7, date(2025, 12, 31), 'revenues', False)
        facts[0]['unit'] = 'CNY'
        for key, bad, message in [('instrument_id', 8, 'identity'),
                                  ('available_at', '2027-01-01T00:00:00', 'timezone')]:
            original = facts[-1][key]
            facts[-1][key] = bad
            with sqlite3.connect(':memory:') as db, self.assertRaisesRegex(ValueError, message):
                exporter.export_snapshot(db, [company], fetch, period, asof)
            facts[-1][key] = original
        for announcement in (None, '2027-01-01T00:00:00Z'):
            original = facts[-1]['available_at']
            facts[-1]['available_at'] = announcement
            with sqlite3.connect(':memory:') as db:
                self.assertEqual(exporter.export_snapshot(db, [company], fetch, period, asof), 1)
                self.assertEqual(db.execute('SELECT count(*) FROM standard_facts').fetchone()[0], len(facts))
            facts[-1]['available_at'] = original
        with sqlite3.connect(':memory:') as db:
            self.assertEqual(exporter.export_snapshot(db, [dict(company, symbols=[])], fetch, period, asof), 0)
            self.assertEqual(db.execute('SELECT status FROM export_universe').fetchone()[0], 'blocked_security_identity')

    def test_failed_publication_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, output = (root/name for name in ('source', 'binary', 'snapshot.sqlite'))
            source.write_bytes(b'unchanged-source')
            binary.write_bytes(b'fixture-binary')
            argv = ['export', '--database', str(source), '--alphalake', str(binary),
                    '--output', str(output), '--period', '2026-06-30',
                    '--as-of', '2026-09-22T00:00:00Z', '--all']
            def snapshot_command(argv, **kwargs):
                rows = Path(argv[argv.index('--output')+1])
                rows.mkdir()
                (rows/'reviewed_zeros.jsonl').write_text('')
                for name in ('companies.jsonl', 'facts.jsonl', 'conflicts.jsonl'):
                    (rows/name).write_text('')
            with patch('sys.argv', argv), patch.object(exporter.subprocess, 'run', side_effect=snapshot_command), patch.object(exporter, 'export_snapshot', side_effect=ValueError('invalid unit')):
                with self.assertRaisesRegex(ValueError, 'invalid unit'):
                    exporter.main()
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob('*.tmp')), [])
            self.assertEqual(source.read_bytes(), b'unchanged-source')
            output.write_bytes(b'keep-existing')
            with patch('sys.argv', argv), patch.object(exporter.subprocess, 'run') as command, patch('sys.stderr'):
                with self.assertRaises(SystemExit):
                    exporter.main()
                command.assert_not_called()
            self.assertEqual(output.read_bytes(), b'keep-existing')


if __name__ == '__main__':
    unittest.main()


def test_standard_bridge_components_and_market_proxy():
    period = date(2026,6,30)
    asof = datetime.fromisoformat('2026-09-26T00:00:00+00:00')
    company = dict(symbols=['sz300866'],symbol_count=1,identifier_count=1,
        exchange_mic='XSHE',instrument_id=7,name='Synthetic',
        quote=dict(close='10',trade_date='2026-06-30',recorded_at='2026-09-25T00:00:00+00:00'))
    def fact(field,value,unit='CNY'):
        return dict(source='tdx',code='300866',instrument_id=7,period=period.isoformat(),field=field,
            canonical_field=field,value=str(value),unit=unit,period_type='instant',statement_scope='provider_default',
            fact_id=field,available_at='2026-08-31T00:00:00+00:00',artifact_sha256='synthetic')
    facts=[fact(f,1000000) for f in exporter.DEBT_COMPONENTS]+[
        fact('cash_and_cash_equivalents',2000000),fact('long_term_equity_investments',3000000),fact('total_shares',1000000,'share')]
    def fetch(code,end):
        return dict(contract_version='alphalake-valuation-v2',code=code,report_period=end.isoformat(),information_as_of=asof.isoformat(),facts=facts,source_conflicts=[])
    indexed={(r['period'],r['field']):r for r in facts}
    assert exporter.cell(indexed,set(),7,period,'bv_debt',False)[:2]==(5,'available')
    assert exporter.cell(indexed,set(),7,period,'cash_and_marketable_securities',False)[:2]==(2,'estimated_partial_scope')
    assert exporter.cell(indexed,set(),7,period,'cross_holdings',False)[:2]==(3,'estimated_partial_scope')
    assert exporter.cell(indexed,set(),7,period,'cash_and_marketable_securities',False)[2][-1]['available_component_million_cny']==2
    del indexed[(period.isoformat(),'lease_liabilities')]
    assert exporter.cell(indexed,set(),7,period,'bv_debt',False)[0] is None
    with sqlite3.connect(':memory:') as db:
        db.row_factory=sqlite3.Row
        exporter.export_snapshot(db,[company],fetch,period,asof,years=1,quarters=1)
        row=db.execute('SELECT * FROM companies').fetchone()
        assert row['stock_price_listing']==10 and row['mv_equity_listing']==10
        assert db.execute("SELECT status FROM export_cells WHERE series='company' AND field='mv_equity_listing'").fetchone()[0]=='reported_share_price_proxy'
    facts.append(fact('listed_h_shares',1,'share'))
    with sqlite3.connect(':memory:') as db:
        exporter.export_snapshot(db,[company],fetch,period,asof,years=1,quarters=1)
        assert db.execute('SELECT mv_equity_listing FROM companies').fetchone()[0] == 10
        assert db.execute("SELECT status FROM export_cells WHERE series='company' AND field='mv_equity_listing'").fetchone()[0]=='a_share_total_share_proxy'
        evidence = json.loads(db.execute("SELECT evidence_json FROM export_cells WHERE series='company' AND field='mv_equity_listing'").fetchone()[0])
        assert evidence['known_foreign_share_classes']['listed_h_shares']['value'] == '1'
    company['quote'] = None
    with sqlite3.connect(':memory:') as db:
        exporter.export_snapshot(db,[company],fetch,period,asof,years=1,quarters=1)
        assert db.execute('SELECT mv_equity_listing FROM companies').fetchone()[0] is None
        assert db.execute("SELECT status FROM export_cells WHERE series='company' AND field='mv_equity_listing'").fetchone()[0] == 'missing_eligible_close'


def test_long_term_subtotal_requires_valid_same_period_components():
    period = date(2026, 6, 30)
    def fact(field, value):
        return dict(instrument_id=7, unit='CNY', period_type='instant', statement_scope='provider_default',
                    value=value, fact_id=field, available_at='2026-08-31T16:00:00Z', artifact_sha256='fixture')
    facts = {(period.isoformat(), f): fact(f, v) for f, v in [
        ('long_term_equity_investments', '556090432'),
        ('other_noncurrent_financial_assets', '665509531.25')]}
    value, status, evidence = exporter.cell(facts, set(), 7, period, 'cross_holdings', False)
    assert value == 1221.59996325 and status == 'estimated_partial_scope'
    assert evidence[-1]['available_component_million_cny'] == 1221.59996325
    assert len(evidence[:-1]) == 2
    assert set(evidence[-1]['missing_components']) == {
        'debt_investments', 'other_debt_investments', 'other_equity_instrument_investments'}
    assert not evidence[-1]['component_arithmetic_complete']
    # 缺主要科目仍展示已有其他组成；不将全缺失变成0。
    del facts[(period.isoformat(), 'long_term_equity_investments')]
    assert exporter.cell(facts, set(), 7, period, 'cross_holdings', False)[2][-1]['available_component_million_cny'] == 665.50953125
    assert exporter.cell({}, set(), 7, period, 'cross_holdings', False)[:2] == (None, 'missing_standard_fact')
    row = facts[(period.isoformat(), 'other_noncurrent_financial_assets')]
    import pytest
    for key, bad in [('unit', 'USD'), ('instrument_id', 8), ('period_type', 'H1')]:
        old = row[key]
        row[key] = bad
        with pytest.raises(ValueError):
            exporter.cell(facts, set(), 7, period, 'cross_holdings', False)
        row[key] = old
    row['value'] = '-1'
    value, status, evidence = exporter.cell(facts, set(), 7, period, 'cross_holdings', False)
    assert value is None and status == 'negative_investment_component_requires_review'
    assert evidence[-1]['value'] == '-1'
    assert exporter.cell(facts, {period.isoformat()}, 7, period, 'cross_holdings', False)[1] == 'source_record_conflict'


def test_annual_effective_tax_rate_preserves_bad_denominators_and_evidence():
    import pytest
    end = date(2025, 12, 31)
    def fact(value):
        return dict(instrument_id=7, unit='CNY', period_type='FY', statement_scope='provider_default', value=value)
    facts = {(end.isoformat(), 'income_tax_expense'): fact('20'), (end.isoformat(), 'profit_before_tax'): fact('100')}
    value, status, evidence = exporter.annual_effective_tax_rate(facts, set(), 7, end)
    assert (value, status) == (.2, 'available') and len(evidence['components']) == 2
    for value in ('0', '-1'):
        facts[(end.isoformat(), 'profit_before_tax')]['value'] = value
        assert exporter.annual_effective_tax_rate(facts, set(), 7, end)[:2] == (None, 'nonpositive_pretax_income')
    facts[(end.isoformat(), 'profit_before_tax')]['value'] = '10'
    assert exporter.annual_effective_tax_rate(facts, set(), 7, end)[1] == 'requires_tax_rate_review'
    assert exporter.annual_effective_tax_rate(facts, {end.isoformat()}, 7, end)[1] == 'source_record_conflict'
    facts[(end.isoformat(), 'income_tax_expense')]['unit'] = 'USD'
    with pytest.raises(ValueError, match='unit'):
        exporter.annual_effective_tax_rate(facts, set(), 7, end)
    assert exporter.annual_effective_tax_rate({}, set(), 7, end)[1] == 'missing_standard_fact'


def test_reviewed_source_zero_is_separate_revocable_evidence():
    import copy
    import pytest
    period = date(2023, 12, 31)
    asof = datetime.fromisoformat('2026-09-27T00:00:00+00:00')
    company = dict(symbols=['sz300866'], symbol_count=1, identifier_count=1,
                   exchange_mic='XSHE', instrument_id=7, name='Synthetic')
    facts = [dict(source='tdx', code='300866', instrument_id=7, period=period.isoformat(),
                  field=f, canonical_field=f, value='1000000', unit='CNY', period_type='instant',
                  statement_scope='provider_default', fact_id=f, available_at='2024-04-25T00:00:00Z',
                  artifact_sha256='a'*64) for f in exporter.DEBT_COMPONENTS if f != 'bonds_payable']
    zero = dict(code='300866', instrument_id=7, period=period.isoformat(), field='bonds_payable',
                value='0', unit='CNY', period_type='instant', statement_scope='consolidated_statement',
                available_at='2024-04-25T00:00:00Z', import_sha256='b'*64, artifact_sha256='a'*64,
                review=dict(code='300866', period=period.isoformat(), value='0', unit='CNY', scope='consolidated_statement',
                            item='reviewed_source_zero_bonds_payable', source_zero=dict(
                                field='bonds_payable', conclusion='explicit_zero_balance', artifact_sha256='a'*64)))
    payload = dict(facts=facts, reviewed_zeros=[zero], source_conflicts=[])
    def fetch(code, end):
        return dict(contract_version='alphalake-valuation-v2', code=code, report_period=end.isoformat(),
                    information_as_of=asof.isoformat(), **payload)
    def run(want):
        with sqlite3.connect(':memory:') as db:
            db.row_factory = sqlite3.Row
            exporter.export_snapshot(db, [company], fetch, period, asof, years=1, quarters=1)
            assert db.execute('SELECT bv_debt FROM financials_annual').fetchone()[0] == want
            assert db.execute("SELECT count(*) FROM standard_facts WHERE field='bonds_payable'").fetchone()[0] == 0
            evidence = json.loads(db.execute("SELECT evidence_json FROM export_cells WHERE series='annual' AND field='bv_debt'").fetchone()[0])
            if want is not None:
                assert [p['import_sha256'] for p in evidence if p.get('kind') == 'reviewed_source_zero'] == ['b'*64]
                assert db.execute('SELECT count(*) FROM reviewed_source_zeros').fetchone()[0] == 1
                assert not any('review' in p for p in evidence)
                data = exporter.target.fetch_company(db, 'SZSE:300866')
                assert data is not None
    run(4)
    payload['reviewed_zeros'] = []; run(None)
    payload['reviewed_zeros'] = [zero]
    payload['source_conflicts'] = [dict(period=period.isoformat())]; run(None)
    payload['source_conflicts'] = []
    for key, value in [('value', '1'), ('instrument_id', 8), ('available_at', '2027-01-01T00:00:00Z')]:
        bad = copy.deepcopy(zero); bad[key] = value; payload['reviewed_zeros'] = [bad]
        with pytest.raises(ValueError, match='reviewed source zero'):
            run(None)
    payload['reviewed_zeros'] = [zero]
    # A supplement never overwrites an existing nonzero standard component.
    indexed = {(r['period'], r['field']): r for r in facts}
    indexed[(period.isoformat(), 'bonds_payable')] = dict(facts[0], field='bonds_payable')
    assert exporter.cell(indexed, set(), 7, period, 'bv_debt', True,
                         {(period.isoformat(), 'bonds_payable'): zero})[:2] == (5, 'available')


def test_anker_explicit_zero_disclosure_is_not_not_applicable():
    import hashlib
    import re
    from pypdf import PdfReader
    root = Path(__file__).resolve().parents[3]/'internal/ingest/testdata/anker-history-2026'
    report = json.loads((root/'reports.json').read_text())['1219800919']
    path = root/report['file']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == report['sha256']
    page = re.sub(r'\s+', '', PdfReader(path).pages[172].extract_text())
    assert '2023年9月子公司提前偿还该债券，截止本年末应付债券余额为0。' in page


def test_missing_debt_preserves_all_components_and_validates_later_values():
    period = date(2024,12,31)
    facts = {(period.isoformat(),field):dict(instrument_id=7,unit='CNY',period_type='instant',
        statement_scope='provider_default',value='1000000',fact_id=i,
        available_at='2025-04-30T16:00:00Z',artifact_sha256='source')
        for i,field in enumerate(exporter.DEBT_COMPONENTS) if i in (0,3,4)}
    value,status,evidence = exporter.cell(facts,set(),7,period,'bv_debt',True)
    assert value is None and status == 'missing_standard_fact'
    assert [r['field'] for r in evidence] == list(exporter.DEBT_COMPONENTS)
    assert [r['field'] for r in evidence if r.get('kind') == 'missing_standard_fact'] == ['long_term_borrowings','bonds_payable']
    assert sum(float(r['value']) for r in evidence if 'value' in r) == 3000000
    facts[(period.isoformat(),'lease_liabilities')]['unit'] = 'USD'
    with unittest.TestCase().assertRaisesRegex(ValueError,'unit'):
        exporter.cell(facts,set(),7,period,'bv_debt',True)
