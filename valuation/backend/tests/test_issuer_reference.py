from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from tools import verify_issuer_reference as tool


@pytest.fixture
def review(tmp_path, monkeypatch):
    catalogue = {'announcements': [dict(announcementId='doc', secCode='001234', orgId='issuer', adjunctUrl='issuer.pdf')]}
    (tmp_path/'catalogue.json').write_text(json.dumps(catalogue))
    (tmp_path/'issuer.pdf').write_bytes(b'PDF evidence')
    text = dict(legal_name_cn='公司的中文名称 公司甲', legal_name_en='公司的外文名称 Issuer Limited',
                a_listing='A股 深圳证券交易所 001234', other_listing='H股 香港联交所 01234',
                exchanges='深圳证券交易所 香港联合交易所')
    event = dict(action='publish', target_ticker='SZSE:001234', source_ticker='SEHK:1234',
        source_locator='By company name!A2:H2', legal_name_cn='公司甲', legal_name_en='Issuer Limited',
        relationship='same_legal_issuer_different_share_class', reviewer='reviewer', review_note='same issuer verified',
        reviewed_at='2020-09-30T00:00:00+00:00',
        document=dict(local_path='issuer.pdf', sha256=tool.digest(tmp_path/'issuer.pdf'),
            catalogue_path='catalogue.json', catalogue_sha256=tool.digest(tmp_path/'catalogue.json'),
            announcement_id='doc', organization_id='issuer', source_url='https://static.cninfo.com.cn/issuer.pdf'),
        evidence={role: dict(page=1, text=value) for role, value in text.items()})
    manifest = dict(contract='alphalake-issuer-reference-review-v1', workbook_sha256='workbook', events=[event])
    universe = dict(contract_version='alphalake-native-coverage-v1', full_source_universe=True,
                    source_universe=1, companies=[dict(ticker='SZSE:001234')])
    monkeypatch.setattr(tool, 'alphalake_company_industry_snapshot', lambda _:dict(sha256='workbook', companies=[]))
    sheet = SimpleNamespace(nrows=2, cell_value=lambda *_:'SEHK:1234',
        row_values=lambda _:['Issuer Limited (SEHK:1234)', 'SEHK:1234', 'Power', 'Utilities', '0', 'China', '', ''])
    monkeypatch.setattr(tool.xlrd, 'open_workbook', lambda *_args, **_kwargs:
        SimpleNamespace(sheet_by_name=lambda _:sheet, release_resources=lambda:None))
    monkeypatch.setattr(tool, 'PdfReader', lambda _:SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda:'\n'.join(text.values()))]))
    return manifest, tmp_path, 'workbook.xls', universe


def test_review_creates_existing_api_input_and_revocation_removes_it(review):
    manifest, workspace, workbook, universe = review
    result = tool.verify(*review)
    assert result['active'][0]['review_sha256'] == tool.signature(manifest['events'][0])
    assert result['active'][0]['request'] == dict(ticker='SZSE:001234', industry_override='Power', country_override='China')
    assert not result['defaults_changed'] and result['valuations_run'] == 0
    manifest['events'].append(dict(action='revoke', target_ticker='SZSE:001234',
        supersedes=tool.signature(manifest['events'][0]), reviewer='reviewer', review_note='identity review withdrawn',
        reviewed_at='2020-09-30T01:00:00+00:00'))
    assert tool.verify(*review)['active'] == []
    assert tool.verify(*review)['revoked'] == ['SZSE:001234']
    (workspace/'issuer.pdf').write_bytes(b'changed evidence')
    assert tool.verify(*review)['active'] == []  # 证据已损坏仍应允许撤销。
    (workspace/'issuer.pdf').write_bytes(b'PDF evidence')
    replacement = deepcopy(manifest['events'][0])
    replacement.update(supersedes=tool.signature(manifest['events'][1]), reviewed_at='2020-09-30T02:00:00+00:00')
    manifest['events'].append(replacement)
    assert len(tool.verify(*review)['active']) == 1


@pytest.mark.parametrize('change,message', [
    (lambda m:m.update(workbook_sha256='changed'), 'workbook hash'),
    (lambda m:m['events'][0].update(relationship='same_group'), 'same legal issuer'),
    (lambda m:m['events'][0].update(legal_name_en='Parent Limited'), 'legal name differs'),
    (lambda m:m['events'][0].update(source_locator='By company name!A3:H3'), 'source row'),
    (lambda m:m['events'][0]['document'].update(sha256='changed'), 'PDF hash'),
    (lambda m:m['events'][0]['document'].update(catalogue_sha256='changed'), 'catalogue hash'),
    (lambda m:m['events'][0]['document'].update(organization_id='parent'), 'provenance'),
    (lambda m:m['events'][0]['document'].update(local_path='../issuer.pdf'), 'leaves workspace'),
    (lambda m:m['events'][0]['evidence']['other_listing'].update(page=0), 'evidence page'),
    (lambda m:m['events'][0]['evidence']['other_listing'].update(text='H股 香港联交所 99999'), 'anchor changed'),
    (lambda m:m['events'][0].update(supersedes='invented'), 'review chain'),
    (lambda m:m['events'][0].update(reviewer=''), 'semantic review'),
    (lambda m:m['events'][0].update(reviewed_at='2026-09-30'), 'timezone-aware'),
    (lambda m:m['events'][0].update(reviewed_at='2099-09-30T00:00:00+00:00'), 'future review'),
])
def test_review_rejects_evidence_or_semantic_changes(review, change, message):
    manifest, workspace, workbook, universe = review
    change(manifest)
    with pytest.raises(ValueError, match=message):
        tool.verify(*review)


def test_review_never_overwrites_exact_source_or_ignores_duplicate_targets(review, monkeypatch):
    manifest, workspace, workbook, universe = review
    monkeypatch.setattr(tool, 'alphalake_company_industry_snapshot',
        lambda _:dict(sha256='workbook', companies=[dict(ticker='SZSE:001234')]))
    with pytest.raises(ValueError, match='missing exact-source'):
        tool.verify(*review)
    universe['companies'] *= 2
    universe['source_universe'] = 2
    with pytest.raises(ValueError, match='duplicate target'):
        tool.verify(*review)


def test_present_pdf_text_still_requires_the_right_share_class_code(review, monkeypatch):
    manifest, workspace, workbook, universe = review
    evidence = manifest['events'][0]['evidence']
    evidence['other_listing']['text'] = 'H股 香港联交所 99999'
    monkeypatch.setattr(tool, 'PdfReader', lambda _:SimpleNamespace(pages=[
        SimpleNamespace(extract_text=lambda:'\n'.join(a['text'] for a in evidence.values()))]))
    with pytest.raises(ValueError, match='other share-class code mismatch'):
        tool.verify(*review)


def test_explicit_hong_kong_listing_role_and_and_typography(review, monkeypatch):
    manifest, workspace, workbook, universe = review
    event = manifest['events'][0]
    event['legal_name_en'] = 'Issuer&Partner Limited'
    event['evidence']['legal_name_en']['text'] = '公司的外文名称 Issuer&Partner Limited'
    sheet = SimpleNamespace(nrows=2, cell_value=lambda *_:'SEHK:1234',
        row_values=lambda _:['Issuer and Partner Limited (SEHK:1234)', 'SEHK:1234', 'Power', 'Utilities', '0', 'China', '', ''])
    monkeypatch.setattr(tool.xlrd, 'open_workbook', lambda *_args, **_kwargs:
        SimpleNamespace(sheet_by_name=lambda _:sheet, release_resources=lambda:None))
    event['evidence']['other_listing']['text'] = '港股上市交易所：香港联交所 港股股票代码：01234'
    monkeypatch.setattr(tool, 'PdfReader', lambda _:SimpleNamespace(pages=[
        SimpleNamespace(extract_text=lambda:'\n'.join(a['text'] for a in event['evidence'].values()))]))
    assert tool.verify(*review)['active'][0]['source_company']['name'].startswith('Issuer and Partner')
    event['evidence']['other_listing']['text'] = '港股报价 香港联交所 01234'
    with pytest.raises(ValueError, match='share-class/exchange role mismatch'):
        tool.verify(*review)
    event['evidence']['other_listing']['text'] = '港股上市交易所：香港联交所 港股股票代码：99999'
    with pytest.raises(ValueError, match='other share-class code mismatch'):
        tool.verify(*review)
    event['legal_name_en'] = 'Issuer&Parent Limited'
    with pytest.raises(ValueError, match='legal name differs'):
        tool.verify(*review)
    assert tool.legal_name_key('Anderson Limited') != tool.legal_name_key('&erson Limited')


def test_ipo_security_code_can_be_bound_by_official_catalogue(review, monkeypatch):
    manifest, workspace, workbook, universe = review
    event = manifest['events'][0]
    catalogue = json.loads((workspace/'catalogue.json').read_text())
    catalogue['announcements'][0]['announcementTitle'] = '首次公开发行股票招股意向书'
    (workspace/'catalogue.json').write_text(json.dumps(catalogue))
    event['document']['catalogue_sha256'] = tool.digest(workspace/'catalogue.json')
    event['evidence']['a_listing'].update(text='本次发行人民币普通股（A股），深圳证券交易所',
                                        identifier_source='catalogue')
    monkeypatch.setattr(tool, 'PdfReader', lambda _:SimpleNamespace(pages=[
        SimpleNamespace(extract_text=lambda:'\n'.join(a['text'] for a in event['evidence'].values()))]))
    assert tool.verify(*review)['active'][0]['target_ticker'] == 'SZSE:001234'
    event['evidence']['a_listing']['text'] += ' 股票代码001235'
    with pytest.raises(ValueError, match='unsupported security identifier evidence'):
        tool.verify(*review)
    event['evidence']['a_listing']['text'] = '本次发行人民币普通股（A股），深圳证券交易所'
    catalogue['announcements'][0]['secCode'] = '001235'
    (workspace/'catalogue.json').write_text(json.dumps(catalogue))
    event['document']['catalogue_sha256'] = tool.digest(workspace/'catalogue.json')
    with pytest.raises(ValueError, match='catalogue announcement target mismatch'):
        tool.verify(*review)
    catalogue['announcements'][0].update(secCode='001234', announcementTitle='年度报告')
    (workspace/'catalogue.json').write_text(json.dumps(catalogue))
    event['document']['catalogue_sha256'] = tool.digest(workspace/'catalogue.json')
    with pytest.raises(ValueError, match='unsupported security identifier evidence'):
        tool.verify(*review)
