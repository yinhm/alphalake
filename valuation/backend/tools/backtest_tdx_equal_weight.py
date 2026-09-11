"""各公司等权的滞后利润校准；不删除极端样本，不改变评价口径。"""
import hashlib
import json

from tools import diagnose_tdx_training_exclusions as prior
from tools.backtest_tdx_training_quality import assess

DIRECTORY = prior.DIRECTORY
PROTOCOL_SHA = '32f0b73f877a9f14eca5f732385089376afcec25c82f18349b8b53742b1afd2d'


def load_inputs():
    raw = (DIRECTORY/'equal-weight-protocol.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA:
        raise ValueError('frozen equal-weight protocol differs')
    p = json.loads(raw)
    if p['source_result']['sha256'] != prior.SOURCE_SHA:
        raise ValueError('parent result differs')
    return p, prior.load_result()


def study(p, parent):
    if list(parent['training']) != p['origins'] or p['minimum_training_after_target_exclusion'] != 30:
        raise ValueError('training contract differs')
    rows, removed = prior.refit(parent, (), equal_weight=True)
    if any(removed.values()):
        raise ValueError('unexpected training removal')
    return dict(protocol_sha256=PROTOCOL_SHA, source_result_sha256=prior.SOURCE_SHA,
        training_counts={o:len(v['admitted']) for o,v in parent['training'].items()},
        **assess(p, parent, rows), results=rows, boundary=p['boundary'])


if __name__ == '__main__':
    print(json.dumps(study(*load_inputs()), ensure_ascii=False, indent=2, allow_nan=False))
