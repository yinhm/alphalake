"""既定预测输入子组的公司重采样；子组外与缺项仍保留在抽样分母。"""
import hashlib
import json

from tools import audit_tdx_cash_scope as scope
from tools import audit_tdx_ocf_uncertainty as base

DIRECTORY = base.DIRECTORY
PROTOCOL_SHA = '494be00c414cfa6d6695c63554d75bef3071c7b57f53d907c5764c2436e545d4'


def load_inputs():
    raw = (DIRECTORY/'scope-uncertainty-protocol.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA:
        raise ValueError('scope uncertainty protocol differs')
    p = json.loads(raw); inputs = {}
    for name, ref in p['inputs'].items():
        raw = (base.ROOT/ref['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError(name+' hash differs')
        inputs[name] = json.loads(raw)
    inherited = base.load_inputs()
    if inputs['parent_uncertainty'] != inherited[0]:
        raise ValueError('parent uncertainty differs')
    return p, inherited, inputs


def study(p, inherited, inputs):
    up, parent, source, receipt = inherited
    reviewed = scope.audit(inputs['scope_protocol'], parent, inputs['forecast_protocol'], source)
    if reviewed['phases'] != inputs['scope_result']['phases']:
        raise ValueError('original scope replay differs')
    original = base.cash.study(parent, source, 'holdout')
    if any(original[k] != receipt[k] for k in ('summary', 'by_origin', 'decision', 'refusals')):
        raise ValueError('original cash replay differs')
    membership = reviewed['phases']['holdout']['membership']
    index = {(r['code'],r['origin']):r for r in membership}
    if len(index) != len(membership):
        raise ValueError('duplicate scope identity')
    rows = [r | dict(origin_scope=index[r['code'],r['origin']]) for r in original['results']]
    selected = [r for r in rows if r['origin_scope']['group'] == p['scope_group']]
    expected = reviewed['phases']['holdout']['by_scope'][p['scope_group']]
    if base.cash.metrics(selected) != expected:
        raise ValueError('scope point estimates differ')
    result = base.analyze(up, parent, rows, scope_group=p['scope_group'])
    result.update(scope_protocol_sha256=PROTOCOL_SHA, scope_boundary=p['boundary'], scope_point_metrics=expected)
    return result


if __name__ == '__main__':
    print(json.dumps(study(*load_inputs()), ensure_ascii=False, indent=2, allow_nan=False))
