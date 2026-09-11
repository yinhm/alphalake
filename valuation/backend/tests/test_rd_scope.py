import copy
import json
import struct

import pytest

from tools.verify_rd_scope import DIRECTORY, load_inputs, verify


def test_real_rd_scope_does_not_substitute_total_or_stock_for_expense():
    ledger, source = load_inputs()
    result = verify(ledger, source)
    assert result == json.loads((DIRECTORY/'rd-scope-result.json').read_bytes())
    assert result[1]['comparisons']['FN304']['source_cny'] == '12791059'
    assert result[1]['total_cny'] == '20698165.13'
    changed = copy.deepcopy(ledger)
    changed['reports'][0]['entries']['capitalized']['values'][0] = '15,990,870.06'
    with pytest.raises(ValueError, match='PDF amounts differ'):
        verify(changed, source)
    for field, replacement in [('FN304', 20698165.13), ('FN34', 7907105.73)]:
        changed = copy.deepcopy(source)
        changed['records'][1]['bits'][field] = struct.unpack('<I', struct.pack('<f', replacement))[0]
        with pytest.raises(ValueError, match=field+' bits differ'):
            verify(ledger, changed)
    changed = copy.deepcopy(source)
    changed['records'].append(copy.deepcopy(changed['records'][0]))
    with pytest.raises(ValueError, match='source identities differ'):
        verify(ledger, changed)
