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
                                  ('available_at', '2027-01-01T00:00:00Z', 'future')]:
            original = facts[-1][key]
            facts[-1][key] = bad
            with sqlite3.connect(':memory:') as db, self.assertRaisesRegex(ValueError, message):
                exporter.export_snapshot(db, [company], fetch, period, asof)
            facts[-1][key] = original
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
    assert exporter.cell(indexed,set(),7,period,'cash_and_marketable_securities',False)[:2]==(None,'partial_target_scope')
    assert exporter.cell(indexed,set(),7,period,'cross_holdings',False)[:2]==(None,'partial_target_scope')
    assert exporter.cell(indexed,set(),7,period,'cash_and_marketable_securities',False)[2][-1]['available_component_million_cny']==2
    del indexed[(period.isoformat(),'lease_liabilities')]
    assert exporter.cell(indexed,set(),7,period,'bv_debt',False)[0] is None
    with sqlite3.connect(':memory:') as db:
        db.row_factory=sqlite3.Row
        exporter.export_snapshot(db,[company],fetch,period,asof,years=1,quarters=1)
        row=db.execute('SELECT * FROM companies').fetchone()
        assert row['stock_price_listing']==10 and row['mv_equity_listing']==10
        assert db.execute("SELECT status FROM export_cells WHERE series='company'").fetchone()[0]=='reported_share_price_proxy'
    facts.append(fact('listed_h_shares',1,'share'))
    with sqlite3.connect(':memory:') as db:
        exporter.export_snapshot(db,[company],fetch,period,asof,years=1,quarters=1)
        assert db.execute('SELECT mv_equity_listing FROM companies').fetchone()[0] is None
        assert db.execute("SELECT status FROM export_cells WHERE series='company'").fetchone()[0]=='requires_share_class_market_values'
