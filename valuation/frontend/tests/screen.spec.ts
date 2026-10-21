import { expect, test, type Page } from '@playwright/test';
import type { ScreenDataset, ScreenResults, ScreenRow } from '../src/types/screen';

const dataset: ScreenDataset = {
  id:'screen-test', title:'合成研究', source_report_id:'batch-test', report_url:'/research/batch-test',
  csv_url:'/api/research/batch-test/files/csv?download=true', research_date:'2026-10-21', financial_period:'2026-06-30',
  information_as_of:'2026-10-20T00:00:00Z', research_frozen_at:'2026-10-21T00:00:00Z', company_evidence_acquired_at:'2026-10-21T00:00:00Z',
  share_dates:{'2026-06-30':65}, quote_dates:{'2026-10-20':65}, condition_type:'合成条件', sqlite_sha256:'a'.repeat(64),
  reference_snapshot_id:'test-reference', runtime_stages:{calculation:'test-calculation',review:'test-review'},
  build_version:'test', built_at:'2026-10-21T01:00:00Z',limitations:[],content_hash:'b'.repeat(64),
};
const rows: ScreenRow[] = Array.from({length:65},(_,index)=>({
  ticker:`SHSE:${600001+index}`,name:`合成公司${index+1}`,reference_industry:'Synthetic',admission:index===2?'blocked_required_inputs':'ready',
  financial_admission:'ready',missing_fields:index===2?['revenues']:[],reason:'synthetic',report_id:'test-company',url:'/research/test-company',mode:'native_conditions_with_company_story',
  condition_value_cny_per_share:index===2?null:index===0?0:100,close_cny:10,close_date:'2026-10-20',
  descriptive_difference_pct:index===1||index===2?null:index===0?-100:900,price_comparison_status:index===1?'unreconciled_post_period_capital_change':'capital_date_proxy',
  price_comparison_reason:'合成来源边界',financial_period:'2026-06-30',share_date:'2026-06-30',information_as_of:dataset.information_as_of,
  research_frozen_at:dataset.research_frozen_at,run_id:'synthetic-run',
  assumptions:{initial_wacc_rate:.08,first_year_growth_rate:.1,first_year_adjusted_margin_rate:.2,first_year_funding_sales_to_capital:2,terminal_growth_rate:.02,terminal_wacc_rate:.08,terminal_pv_share_rate:.6},
  assumption_missing_reasons:{},review_flags:index===0?['nonpositive_equity_condition']:[],comparison_display_status:index===2?'not_calculated':index===1?'unreconciled_post_period_capital_change':'capital_date_proxy',
  comparison_display_reason:'合成边界',model_source:{path:'research/test-company/valuation.json',sha256:'a'.repeat(64)},
}));
const labels = {admission:{ready:'已计算',blocked_required_inputs:'缺财务输入',requires_scope_review:'待范围审核',outside_product_scope:'产品范围外'},
  flag:{nonpositive_equity_condition:'条件权益非正'},comparison:{capital_date_proxy:'资本基期代理',unreconciled_post_period_capital_change:'期后资本变化未衔接',not_calculated:'未计算／未比较'}};

async function mock(page:Page,empty=false) {
  const unexpected:string[]=[];
  const queries:URLSearchParams[]=[];
  await page.route(/^http:\/\/127\.0\.0\.1:4173\/api\//,async route=>{
    const url=new URL(route.request().url());
    if(url.pathname==='/api/knowledge/index')return route.fulfill({json:{status:'ready',release_id:'test',schema_version:1,terms:[]}});
    if(url.pathname.startsWith('/api/admin/'))return route.fulfill({json:{configured:false,admin:false}});
    if(url.pathname==='/api/valuation-results/datasets')return route.fulfill({json:{contract:'alphalake-valuation-results-v1',current:empty?null:dataset.id,datasets:empty?[]:[{...dataset,universe_count:65}]}});
    if(url.pathname===`/api/valuation-results/${dataset.id}`){
      queries.push(url.searchParams);
      if(url.searchParams.get('sort')==='bad')return route.fulfill({status:422,json:{detail:{reason:'未知排序条件'}}});
      const q=(url.searchParams.get('q')??'').normalize('NFKC');
      const selected=rows.filter(row=>(row.name+' '+row.ticker).includes(q));
      if(url.searchParams.get('direction')==='desc')selected.reverse();
      const offset=Number(url.searchParams.get('offset')??0),limit=Number(url.searchParams.get('limit')??50);
      const body:ScreenResults={contract:'alphalake-valuation-results-v1',dataset,universe_count:65,filtered_count:selected.length,
        admission_counts:{ready:64,blocked_required_inputs:1},labels,
        facets:{admission:{ready:64,blocked_required_inputs:1,requires_scope_review:0,outside_product_scope:0},industry:{Synthetic:65},
          flag:{nonpositive_equity_condition:1},comparison:{capital_date_proxy:63,unreconciled_post_period_capital_change:1,not_calculated:1}},
        range_unknown_counts:url.searchParams.has('wacc_max_rate')?{wacc:1}:{},offset,limit,rows:selected.slice(offset,offset+limit),issues:[]};
      return route.fulfill({json:body});
    }
    if(url.pathname==='/api/research/test-company')return route.fulfill({json:{
      report:{contract:'alphalake-research-v1',id:'test-company',title:'合成公司报告',summary:'合成说明',kind:'initial_coverage',status:'complete',tickers:['SHSE:600001'],published_at:'2026-10-21T00:00:00Z',research_date:'2026-10-21',financial_period:'2026-06-30',information_as_of:dataset.information_as_of,share_date:'2026-06-30',run_ids:[],limitations:[],previous_id:null,report_file:'report'},
      files:[],document:{id:'report',label:'研究正文',path:'research/test-company/report.md',body_md:'## 合成研究正文'},
    }});
    unexpected.push(url.pathname);return route.fulfill({status:500,json:{detail:'unexpected'}});
  });
  return {unexpected,queries};
}

test('screen loads without a company; search, URL restore, pagination and report return',async({page})=>{
  const {unexpected,queries}=await mock(page);
  await page.goto('/screen');
  await expect(page.getByRole('heading',{name:'选股',exact:true})).toBeVisible();
  await expect(page).toHaveURL(/dataset=screen-test/);
  await expect(page.getByRole('table').getByRole('row')).toHaveCount(51);
  const tableSize = await page.locator('.screen-scroll').evaluate(element => ({
    content: element.scrollHeight, visible: element.clientHeight, viewport: window.innerHeight,
  }));
  expect(tableSize.content).toBeLessThanOrEqual(tableSize.visible + 1);
  expect(tableSize.visible).toBeGreaterThan(tableSize.viewport);
  await page.getByRole('button',{name:'下一页',exact:true}).click();
  await expect(page.getByRole('table').getByRole('row')).toHaveCount(16);
  await page.getByRole('searchbox',{name:'搜索公司'}).fill('600001');
  await expect(page.getByRole('table').getByRole('row')).toHaveCount(2);
  expect(queries.at(-1)?.get('q')).toBe('600001');
  await page.reload();
  await expect(page.getByRole('link',{name:'合成公司1',exact:true})).toBeVisible();
  await page.getByRole('link',{name:'合成公司1',exact:true}).click();
  await expect(page.getByRole('heading',{name:'合成公司报告'})).toBeVisible();
  await page.getByRole('link',{name:'返回选股列表'}).click();
  await expect(page.getByRole('searchbox',{name:'搜索公司'})).toHaveValue('600001');
  await expect(page.getByRole('table').getByRole('row')).toHaveCount(2);
  expect(unexpected).toEqual([]);
});

test('numeric percent units, multi filters, assumptions, null comparison and real zero',async({page})=>{
  const {queries,unexpected}=await mock(page);
  await page.goto('/screen?dataset=screen-test');
  await expect(page.getByRole('table')).toBeVisible();
  const zero=page.getByRole('row').filter({has:page.getByRole('link',{name:'合成公司1',exact:true})});
  await expect(zero.getByRole('cell',{name:'0.00',exact:true})).toBeVisible();
  const changed=page.getByRole('row').filter({has:page.getByRole('link',{name:'合成公司2',exact:true})});
  await expect(changed).toContainText('期后资本变化未衔接');
  await expect(changed).not.toContainText('900.00%');
  await page.getByText('数值范围筛选',{exact:true}).click();
  await page.getByRole('spinbutton',{name:'初始WACC（%）上限'}).fill('10');
  await page.getByRole('spinbutton',{name:'描述性价差（%）下限'}).fill('20');
  await page.getByRole('button',{name:'应用数值范围'}).click();
  await expect.poll(()=>queries.at(-1)?.get('wacc_max_rate')).toBe('0.1');
  expect(queries.at(-1)?.get('difference_min_pct')).toBe('20');
  // URL状态通过路由transition提交；检查提交后的复选状态。
  await page.getByLabel('显示关键假设').click();
  await expect(page.getByLabel('显示关键假设')).toBeChecked();
  await expect(page.getByRole('columnheader',{name:'首年增长'})).toBeVisible();
  await page.getByRole('button',{name:/每股条件价值/}).click();
  await expect(page.getByRole('columnheader',{name:/每股条件价值/})).toHaveAttribute('aria-sort','ascending');
  await page.getByRole('button',{name:/每股条件价值/}).click();
  await expect.poll(()=>queries.at(-1)?.get('direction')).toBe('desc');
  await page.getByRole('button',{name:'重置筛选'}).click();
  await expect(page).toHaveURL(/\/screen\?dataset=screen-test$/);
  await page.getByText('计算状态',{exact:true}).first().click();
  await page.getByRole('checkbox',{name:/已计算/}).click();
  await expect(page.getByRole('checkbox',{name:/已计算/})).toBeChecked();
  await page.getByRole('checkbox',{name:/缺财务输入/}).click();
  await expect(page.getByRole('checkbox',{name:/缺财务输入/})).toBeChecked();
  await expect.poll(()=>queries.at(-1)?.getAll('admission').length).toBe(2);
  expect(unexpected).toEqual([]);
});

test('mobile details expose hidden columns, keyboard works, empty/error states are explicit',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await mock(page);
  await page.goto('/screen?dataset=screen-test&q=600001');
  await expect(page.getByRole('table')).toBeVisible();
  await page.getByRole('button',{name:'详情 ＋'}).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('合成公司1 · 输入与适用边界')).toBeVisible();
  await expect(page.locator('.screen-row-detail')).toContainText('初始WACC');
  await expect(page.locator('.screen-row-detail')).toContainText('Synthetic');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.getByRole('searchbox',{name:'搜索公司'}).fill('没有这家公司');
  await expect(page.getByText('没有匹配的公司，请调整或重置筛选条件。')).toBeVisible();
  await page.goto('/screen?dataset=screen-test&sort=bad');
  await expect(page.getByRole('alert')).toContainText('未知排序条件');
});

test('no publication does not trigger a valuation fallback',async({page})=>{
  const {unexpected}=await mock(page,true);
  await page.goto('/screen');
  await expect(page.getByRole('status')).toContainText('尚未发布估值结果表');
  expect(unexpected).toEqual([]);
});
