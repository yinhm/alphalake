"""产品范围只读政策：分类证据与财务完整性分开，不改变公司事实。"""

POLICY = 'nonfinancial-tdx-damodaran-2026-v1'
FINANCIAL_INDUSTRIES = frozenset({
    'Bank (Money Center)', 'Banks (Regional)', 'Brokerage & Investment Banking',
    'Financial Svcs. (Non-bank & Insurance)', 'Insurance (General)',
    'Insurance (Life)', 'Insurance (Prop/Cas.)', 'Investments & Asset Management',
})
TDX_FINANCIAL_NODES = {
    'X500101': '国有大型银行', 'X500102': '股份制银行', 'X500201': '城商行', 'X500202': '农商行',
    'X5101': '证券', 'X5102': '保险', 'X510301': '非货币银行', 'X510302': '信托',
    'X510303': '投资管理', 'X510304': '期货', 'X510305': '租赁', 'X510306': '金融控股', 'X510399': '其他金融服务',
}


def scope_status(reference_status, industry, financial_nodes):
    if reference_status != 'verified_source' or industry is None:
        return 'outside_financial_scope' if financial_nodes else 'unresolved_industry_scope'
    if financial_nodes and industry not in FINANCIAL_INDUSTRIES:
        return 'unresolved_industry_scope'
    return 'outside_financial_scope' if industry in FINANCIAL_INDUSTRIES else 'nonfinancial_by_reference'


def scope_context(company, reference):
    members = company.get('industry_memberships') or []
    official = [m for m in members if m['source'] == 'damodaran' and m['taxonomy_code'] == 'damodaran_industry_2026']
    nodes = sorted({m['node_code'] for m in members if m['source'] == 'tdx'
        and m['taxonomy_code'] == 'tdx_shenwan_industry'
        and TDX_FINANCIAL_NODES.get(m['node_code']) == m['node_name']})
    industry = official[0]['node_name'] if len(official) == 1 else None
    return dict(policy=POLICY, status=scope_status(reference.get('status'), industry, nodes),
        reference_status=reference.get('status', 'not_published'),
        reference_release_id=reference.get('release_id'), industry=industry, tdx_financial_nodes=nodes)


def company_scope(company, reference):
    return scope_context(company, reference)['status'], company.get('industry_memberships') or []


def validate_scope(scope):
    if (not isinstance(scope, dict) or set(scope) != {'policy', 'status', 'reference_status',
            'reference_release_id', 'industry', 'tdx_financial_nodes'} or scope['policy'] != POLICY
            or not isinstance(scope['reference_status'], str)
            or scope['reference_release_id'] is not None and (type(scope['reference_release_id']) is not int or scope['reference_release_id'] <= 0)
            or scope['industry'] is not None and (not isinstance(scope['industry'], str) or not scope['industry'])
            or not isinstance(scope['tdx_financial_nodes'], list)
            or any(not isinstance(n, str) or n not in TDX_FINANCIAL_NODES for n in scope['tdx_financial_nodes'])
            or scope['tdx_financial_nodes'] != sorted(set(scope['tdx_financial_nodes']))
            or scope['status'] != scope_status(scope['reference_status'], scope['industry'], scope['tdx_financial_nodes'])):
        raise ValueError('Invalid valuation scope context; rebuild the snapshot')


def attach_scope_diagnostic(diagnostic, scope, industry_override=None):
    validate_scope(scope)
    result = dict(diagnostic, financial_status=diagnostic.get('financial_status', diagnostic['status']), model_scope=scope)
    financial = bool(scope['tdx_financial_nodes']) or scope['industry'] in FINANCIAL_INDUSTRIES
    if scope['status'] == 'outside_financial_scope' or industry_override in FINANCIAL_INDUSTRIES:
        status, reason = 'outside_product_scope', '金融或金融分类冲突不适用当前非金融DCF；保留财务数据，等待专项模型'
    elif financial:
        status, reason = 'requires_scope_review', '金融与非金融来源分类冲突；先审核范围，行业覆盖不能抹除已有金融证据'
    elif diagnostic['status'] != 'ready' or scope['status'] == 'nonfinancial_by_reference':
        return result
    elif industry_override:
        result['model_scope'] = dict(scope, admission_basis='explicit_nonfinancial_industry_condition',
            industry_condition=industry_override)
        result['warnings'] = [*result['warnings'], '行业未获来源确认；仅按用户显式非金融行业条件计算，不改公司分类事实']
        return result
    else:
        status, reason = 'requires_scope_review', '公司非金融范围未确认；须核对分类或显式提供非金融行业条件'
    return dict(result, status=status, blockers=[reason, *result['blockers']])
