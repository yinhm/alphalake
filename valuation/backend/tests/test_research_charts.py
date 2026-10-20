"""验证图表保留缺项、来源、桥接关系及冻结报告边界；不运行DCF。"""
from copy import deepcopy
import json

import pytest

pytest.importorskip('matplotlib', reason='研究制图的可选research依赖未安装；CI显式安装')
from PIL import Image

from tools.research_charts import CONTRACT, digest, render
from data_sources.research_reports import publish, verified_file


@pytest.fixture
def packet(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    source = tmp_path/'source.json'
    source.write_text(json.dumps({'actual': [1, None, 3], 'forecast': [3, 4, 5]}))
    chart = dict(id='revenue', kind='line', title='收入的事实与条件', unit='亿元',
                 period='合成年度，不是公司预测', takeaway='空值保留断点，预测不冒充事实。',
                 labels=['2024年', '2025年', '2026年'], source_ids=['source'],
                 series=[dict(label='收入', role='reported', values=[1, None, 3])])
    payload = dict(contract=CONTRACT, company='合成研究',
                   sources=[dict(id='source', label='合成来源', path='source.json', sha256=digest(source),
                                 locator='actual；测试数值，不是公司事实')], charts=[chart])
    return tmp_path, payload


def test_pngs_keep_source_dataset_and_can_be_published_as_research(packet):
    ws, payload = packet
    bar = deepcopy(payload['charts'][0]);bar.update(id='cash', kind='bar')
    bar['series'] = [dict(label='条件现金流', role='forecast', values=[1, -1, 3])]
    bridge = dict(id='bridge', kind='waterfall', title='已有价值桥', unit='亿元', period='合成基期',
                  takeaway='变动须与给定终点闭合。', source_ids=['source'], labels=['经营', '债务', '股权'],
                  values=[10, -4, 6], types=['total', 'change', 'total'])
    heat = dict(id='sensitivity', kind='heatmap', title='已计算敏感性', unit='元/股', period='合成条件',
                takeaway='不插值或重算。', source_ids=['source'], labels=['条件A', '条件B'],
                row_labels=['风险低', '风险高'], values=[[2, 3], [1, 2]])
    payload['charts'] += [bar, bridge, heat]
    folder = ws/'research'/'example';out = folder/'charts'
    result = render(payload, out)
    assert result['model_calculated'] is False and len(result['files']) == 4
    assert json.loads((out/'chart-data.json').read_text()) == payload
    assert (out/'gallery.md').read_text().count('![') == 4
    for file in result['files']:
        path = ws/file['path']
        assert digest(path) == file['sha256']
        with Image.open(path) as im:
            assert im.width >= 1500 and im.height >= 900
            assert im.convert('L').getextrema()[0] < 150  # 图像非空白。
    (folder/'report.md').write_text('# 合成报告\n\n![收入](charts/revenue.png)\n')
    report = publish(folder, dict(contract='alphalake-research-v1', id='example', title='图表验收',
        summary='仅验证附件链', kind='research_note', files=[
            dict(id='report', label='正文', path='research/example/report.md'),
            *[{k:f[k] for k in ['id', 'label', 'path', 'sha256']} for f in result['files']]]))
    assert verified_file(report, 'revenue')[1] == out/'revenue.png'
    with pytest.raises(ValueError, match='冻结'):
        render(payload, folder/'second')


@pytest.mark.parametrize('values', [[True, 2, 3], [1, float('nan'), 3], [1, float('inf'), 3], [None]*3, [1, 2]])
def test_invalid_values_never_become_zero_or_create_outputs(packet, values):
    ws, payload = packet
    payload['charts'][0]['series'][0]['values'] = values
    with pytest.raises(ValueError):
        render(payload, ws/'research'/'bad'/'charts')
    assert not (ws/'research'/'bad'/'charts').exists()


def test_source_tampering_and_escape_are_rejected(packet):
    ws, payload = packet
    (ws/'source.json').write_text('changed')
    with pytest.raises(ValueError, match='SHA-256'):
        render(payload, ws/'research'/'bad'/'charts')
    payload['sources'][0]['path'] = '../outside.json'
    with pytest.raises(ValueError, match='相对路径'):
        render(payload, ws/'research'/'bad'/'charts')


def test_zero_bridge_does_not_hide_mismatched_total(packet):
    ws, payload = packet
    payload['charts'][0].update(kind='waterfall', labels=['起点', '终点'], values=[0, 10], types=['total', 'total'])
    with pytest.raises(ValueError, match='不闭合'):
        render(payload, ws/'research'/'bad'/'charts')


def test_existing_output_is_not_overwritten(packet):
    ws, payload = packet
    out = ws/'research'/'example'/'charts';out.mkdir(parents=True)
    (out/'marker').write_text('keep')
    with pytest.raises(ValueError, match='覆盖'):
        render(payload, out)
    assert (out/'marker').read_text() == 'keep'
