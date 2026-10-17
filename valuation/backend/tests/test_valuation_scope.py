import pytest
from data_sources.valuation_scope import scope_context, validate_scope, attach_scope_diagnostic


def test_scope_context_requires_consistent_current_policy():
    scope = scope_context({}, {})
    validate_scope(scope)
    for key, value in [('status', 'nonfinancial_by_reference'), ('policy', 'unreviewed'),
                       ('tdx_financial_nodes', ['unknown']), ('industry', 123), ('reference_release_id', True)]:
        with pytest.raises(ValueError, match='Invalid valuation scope'):
            validate_scope(scope | {key: value})
    diagnostic = dict(status='blocked_required_inputs', blockers=['missing revenue'], warnings=[])
    explicit = attach_scope_diagnostic(diagnostic, scope, 'Retail (General)')
    assert explicit['status'] == 'blocked_required_inputs'  # Explicit scope never fills financial gaps.
    assert diagnostic == dict(status='blocked_required_inputs', blockers=['missing revenue'], warnings=[])
