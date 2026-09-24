import { useState, useRef, useEffect } from 'react';
import {
  searchCompanies, fetchFromFile,
  companyExists, valueFromDatabase,
} from '../api/client';
import type { SearchResult, TDXPolicyDocument } from '../api/client';
import type { ValuationResponse } from '../types/valuation';

/**
 * 2-step onboarding wizard for starting a new valuation.
 *
 *   Step 1 — Search & select a company
 *   Step 2 — Pick a path:
 *              A) Value directly from the built-in database (instant), OR
 *              B) Upload a data file received from the repository maintainer.
 *
 * Only the current step is visible. Completed steps collapse to a compact
 * summary with a "change" link for back-nav. The user cannot skip ahead.
 */

type Step = 1 | 2;
type DatabaseStatus = { inDb: boolean; dataAsOf: string | null; requiresPolicy: boolean; reportPeriod: string | null };

// 页面初始情景，不是公司审核结论或市场参数；提交后由后端校验并留痕。
const scenarioFields: [string, string, number][] = [
  ['wacc', 'WACC（%）', 10], ['tax_rate', '所得税率（%）', 25],
  ['growth_floor', '增长下限（%）', -10], ['growth_ceiling', '增长上限（%）', 20],
  ['growth_shift', '历史增长调整（百分点）', 0], ['margin_shift', '目标利润率调整（百分点）', 0],
  ['sales_to_capital', '收入／资本倍率', 3], ['terminal_growth', '永续增长率（%）', 2],
  ['terminal_roic', '终值 ROIC（%）', 10], ['cash_recovery', '现金回收比例（%）', 80],
  ['operating_cash_ratio', '经营现金／收入（%）', 3], ['minority_book_multiple', '少数股权账面倍率', 1],
  ['debt_book_multiple', '债务账面倍率', 1], ['extra_dilution_rate', '额外股本稀释（%）', 2],
  ['additional_claims_million_cny', '附加索偿（百万元）', 0],
];
const scale = (label: string) => label.includes('%') || label.includes('百分点') ? 100 : 1;
function defaultPolicy(code: string, period: string): TDXPolicyDocument {
  return { code, policy: {
    ...Object.fromEntries(scenarioFields.map(([key, label, value]) => [key, value / scale(label)])),
    policy_id: 'nonfinancial-history-fcff-v1', approved_report_period: period,
    scenario: 'web_manual_scenario_v1',
    review_note: '网页默认或用户手工调整的条件情景；参数未经公司专项审核，不是市场估计或目标价。',
    nonfinancial_scope_review: '用户选择普通非金融模型作为条件情景；仍执行标准金融业务字段检查，不声称完成公司范围审核。',
    bridge_basis: 'report_date_book_debt_no_conversion_scenario',
    financial_asset_policy: 'no_credit_pending_classification',
    bridge_review: '财报日股本与账面债务、不转股；未分类金融资产暂不计价。附加索偿默认0仅为假设，不证明没有义务。',
  } };
}

interface Props {
  onComplete: (response: ValuationResponse) => void;
  onDemo: () => void;
}

export default function OnboardingWizard({ onComplete, onDemo }: Props) {
  const [step, setStep] = useState<Step>(1);
  const [searchQuery, setSearchQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null);
  const [selectedCompany, setSelectedCompany] = useState<SearchResult | null>(null);

  // DB-backed path: when the selected company is in the ingested markets DB,
  // offer an instant valuation with no file round-trip.
  const [dbStatus, setDbStatus] = useState<DatabaseStatus | null>(null);
  const [tdxPolicy, setTdxPolicy] = useState<TDXPolicyDocument | undefined>();
  const [loadingFromDb, setLoadingFromDb] = useState(false);

  useEffect(() => {
    setTdxPolicy(undefined);
    setDbStatus(null);
    if (!selectedCompany) { setDbStatus(null); return; }
    let cancelled = false;
    companyExists(selectedCompany.exchange_ticker)
      .then((r) => { if (!cancelled) {
        setDbStatus({ inDb: r.in_database, dataAsOf: r.data_as_of, requiresPolicy: r.requires_tdx_policy, reportPeriod: r.report_period });
        if (r.requires_tdx_policy && r.report_period) setTdxPolicy(defaultPolicy(selectedCompany.symbol, r.report_period));
      } })
      .catch(() => { if (!cancelled) setError('无法读取所选数据库，请检查快照版本与服务状态。'); });
    return () => { cancelled = true; };
  }, [selectedCompany]);

  async function triggerValueFromDb() {
    if (!selectedCompany) return;
    setLoadingFromDb(true);
    setError(null);
    try {
      const resp = await valueFromDatabase(selectedCompany.exchange_ticker, tdxPolicy);
      onComplete(resp);
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string | { status?: string; reason?: string; missing?: string[] } } } }).response?.data?.detail;
      setError(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(item => String(item.msg ?? item)).join('；') : detail ? [detail.status, detail.reason, detail.missing?.join('、')].filter(Boolean).join('：') : e instanceof Error ? e.message : 'Database valuation failed');
    } finally {
      setLoadingFromDb(false);
    }
  }

  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── step 1: search ──
  async function runSearch() {
    const q = searchQuery.trim();
    if (!q) return;
    setSearching(true);
    setError(null);
    setSearchResults(null);
    try {
      const results = await searchCompanies(q);
      setSearchResults(results);
      if (results.length === 1) {
        setSelectedCompany(results[0]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed');
    } finally {
      setSearching(false);
    }
  }

  function pickCompany(c: SearchResult) {
    setSelectedCompany(c);
  }

  function proceedToStep2() {
    if (!selectedCompany) return;
    setStep(2);
    setError(null);
  }

  // ── step 2: upload ──
  async function handleFile(file: File) {
    if (!file) return;
    const okExt = /\.(xlsx|xls)$/i.test(file.name);
    if (!okExt) {
      setError('Please upload a .xlsx or .xls file.');
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const resp = await fetchFromFile(file, 'US', 0.0425);
      onComplete(resp);
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'response' in err) {
        const axErr = err as { response?: { data?: { detail?: string } } };
        setError(axErr.response?.data?.detail || 'Failed to load file');
      } else {
        setError(err instanceof Error ? err.message : 'Failed to load file');
      }
    } finally {
      setUploading(false);
    }
  }

  const handleUploadChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    e.target.value = '';
  };

  function goBack(target: Step) {
    setStep(target);
    setError(null);
  }

  const steps: { num: Step; title: string; subtitle: string }[] = [
    { num: 1, title: 'Find company', subtitle: selectedCompany ? selectedCompany.company_name : '' },
    { num: 2, title: 'Start valuation', subtitle: '' },
  ];

  return (
    <div className="max-w-3xl mx-auto py-8 px-4 space-y-6">
      <div className="text-center">
        <h1 className="text-3xl font-bold text-gray-800 mb-1">Investment Valuation Agent</h1>
        <p className="text-gray-500 text-sm">
          Pick a company, then value it instantly from the built-in database or upload a data file.
        </p>
      </div>

      <div className="flex items-center gap-0">
        {steps.map((s, i) => {
          const active = s.num === step;
          const done = s.num < step;
          const bg = done ? 'bg-green-600' : active ? 'bg-blue-600' : 'bg-gray-300';
          const textColor = done || active ? 'text-gray-900 font-semibold' : 'text-gray-400';
          return (
            <div key={s.num} className="flex items-center flex-1">
              <div className="flex flex-col items-center flex-1 cursor-pointer" onClick={() => done && goBack(s.num)}>
                <div className={`w-8 h-8 rounded-full ${bg} text-white flex items-center justify-center text-sm font-bold shrink-0`}>
                  {done ? '✓' : s.num}
                </div>
                <div className={`mt-1.5 text-xs ${textColor} text-center`}>{s.title}</div>
                {s.subtitle && (
                  <div className="text-[10px] text-gray-400 text-center max-w-[180px] truncate">{s.subtitle}</div>
                )}
              </div>
              {i < steps.length - 1 && (
                <div className={`h-0.5 ${done ? 'bg-green-600' : 'bg-gray-200'} flex-1 mt-[-22px]`} />
              )}
            </div>
          );
        })}
      </div>

      {error && (
        <div role="alert" className="bg-red-50 border border-red-200 rounded-lg px-4 py-3">
          <p className="text-red-700 text-sm">{error}</p>
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
        {step === 1 && (
          <StepOne
            searchQuery={searchQuery}
            setSearchQuery={setSearchQuery}
            searching={searching}
            searchResults={searchResults}
            selectedCompany={selectedCompany}
            onSearch={runSearch}
            onPick={pickCompany}
            onProceed={proceedToStep2}
          />
        )}
        {step === 2 && selectedCompany && (
          <StepTwo
            company={selectedCompany}
            dbStatus={dbStatus}
            tdxPolicy={tdxPolicy}
            onPolicy={setTdxPolicy}
            loadingFromDb={loadingFromDb}
            onValueFromDb={triggerValueFromDb}
            uploading={uploading}
            fileInputRef={fileInputRef}
            onUploadChange={handleUploadChange}
            onDropFile={handleFile}
            onBack={() => goBack(1)}
          />
        )}
      </div>

      <div className="flex items-center justify-center gap-4 text-xs text-gray-400">
        <button
          onClick={onDemo}
          className="text-gray-500 hover:text-gray-700 underline"
        >
          Or try the demo data
        </button>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Step 1 — Find company
// ──────────────────────────────────────────────────────────────────────
function StepOne({
  searchQuery, setSearchQuery, searching, searchResults, selectedCompany,
  onSearch, onPick, onProceed,
}: {
  searchQuery: string; setSearchQuery: (s: string) => void;
  searching: boolean; searchResults: SearchResult[] | null;
  selectedCompany: SearchResult | null;
  onSearch: () => void;
  onPick: (c: SearchResult) => void;
  onProceed: () => void;
}) {
  return (
    <div className="p-6 space-y-4">
      <div>
        <h2 className="text-lg font-bold text-gray-800 mb-1">Step 1 — Find the company</h2>
        <p className="text-xs text-gray-500">
          Search by ticker, company name, or <code className="bg-gray-100 px-1 rounded text-[11px]">Exchange:Ticker</code> format.
          Examples: <code className="bg-gray-100 px-1 rounded text-[11px]">NVDA</code>, <code className="bg-gray-100 px-1 rounded text-[11px]">Alibaba</code>, <code className="bg-gray-100 px-1 rounded text-[11px]">SEHK:992</code>.
        </p>
      </div>

      <div className="flex gap-2">
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onSearch()}
          placeholder="Ticker or company name…"
          className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          autoFocus
        />
        <button
          onClick={onSearch}
          disabled={!searchQuery.trim() || searching}
          className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm font-semibold hover:bg-blue-700 disabled:bg-gray-300"
        >
          {searching ? 'Searching…' : 'Search'}
        </button>
      </div>

      {searchResults && (
        <div>
          {searchResults.length === 0 ? (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-900">
              No matches. Try a different ticker or spelling, or use the exchange-prefix format like <code className="bg-white px-1 rounded">NasdaqGS:MSFT</code>.
            </div>
          ) : (
            <div>
              <div className="text-xs text-gray-500 mb-2">
                {searchResults.length} {searchResults.length === 1 ? 'match' : 'matches'} — select the correct one:
              </div>
              <div className="border border-gray-200 rounded-lg overflow-hidden max-h-72 overflow-y-auto">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-[11px] text-gray-500 sticky top-0">
                    <tr>
                      <th className="px-3 py-1.5 text-left">Exchange:Ticker</th>
                      <th className="px-3 py-1.5 text-left">Company</th>
                      <th className="px-3 py-1.5 text-left">Country</th>
                      <th className="px-3 py-1.5 text-left">Industry</th>
                    </tr>
                  </thead>
                  <tbody>
                    {searchResults.map((r) => {
                      const isSelected = selectedCompany?.exchange_ticker === r.exchange_ticker;
                      return (
                        <tr
                          key={r.exchange_ticker}
                          onClick={() => onPick(r)}
                          className={`cursor-pointer border-b border-gray-100 last:border-0 ${
                            isSelected ? 'bg-blue-100 font-semibold' : 'hover:bg-blue-50'
                          }`}
                        >
                          <td className="px-3 py-2 font-mono text-xs">{r.exchange_ticker}</td>
                          <td className="px-3 py-2">{r.company_name}</td>
                          <td className="px-3 py-2 text-xs">{r.country}</td>
                          <td className="px-3 py-2 text-xs">{r.industry}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {selectedCompany && (
        <div className="flex justify-end pt-2 border-t border-gray-100">
          <button
            onClick={onProceed}
            className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm font-semibold hover:bg-blue-700"
          >
            Continue →
          </button>
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Step 2 — Value from DB (if available) or upload a data file
// ──────────────────────────────────────────────────────────────────────
function StepTwo({
  company, dbStatus, tdxPolicy, onPolicy, loadingFromDb, onValueFromDb,
  uploading, fileInputRef, onUploadChange, onDropFile, onBack,
}: {
  company: SearchResult;
  dbStatus: DatabaseStatus | null;
  tdxPolicy: TDXPolicyDocument | undefined;
  onPolicy: (policy: TDXPolicyDocument | undefined) => void;
  loadingFromDb: boolean;
  onValueFromDb: () => void;
  uploading: boolean;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onUploadChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onDropFile: (file: File) => void;
  onBack: () => void;
}) {
  const [dragging, setDragging] = useState(false);

  const handleDragOver = (e: React.DragEvent<HTMLLabelElement>) => {
    e.preventDefault(); e.stopPropagation();
    if (!dragging) setDragging(true);
  };
  const handleDragLeave = (e: React.DragEvent<HTMLLabelElement>) => {
    e.preventDefault(); e.stopPropagation();
    setDragging(false);
  };
  const handleDrop = (e: React.DragEvent<HTMLLabelElement>) => {
    e.preventDefault(); e.stopPropagation();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) onDropFile(file);
  };

  const zoneState = uploading
    ? 'border-blue-400 bg-blue-50'
    : dragging
      ? 'border-blue-600 bg-blue-100'
      : 'border-gray-300 hover:border-blue-500 hover:bg-blue-50/50';

  return (
    <div className="p-6 space-y-4">
      <div>
        <h2 className="text-lg font-bold text-gray-800 mb-1">Step 2 — Start the valuation</h2>
      </div>

      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
        <div className="text-xs text-blue-700 uppercase font-semibold mb-1">Selected company</div>
        <div className="font-bold text-blue-900 text-lg">{company.company_name}</div>
        <div className="text-sm text-blue-700 mt-1">
          <span className="font-mono">{company.exchange_ticker}</span>
          <span className="mx-2 text-blue-400">·</span>
          {company.country}
          <span className="mx-2 text-blue-400">·</span>
          {company.industry}
        </div>
      </div>

      {/* Path A — database-backed instant valuation. */}
      {dbStatus?.inDb && (
        <div className="bg-emerald-50 border border-emerald-300 rounded-lg p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs text-emerald-800 uppercase font-semibold mb-1">
                Option A — Instant from database
              </div>
              <div className="text-sm text-emerald-900">
                {dbStatus.requiresPolicy ? '已填入默认情景，可直接计算或先修改下方参数。财务缺项与金融兼营不适用仍将明确拒绝。' : 'This company is already in the built-in dataset. Run the valuation in one click.'}
              </div>
              {dbStatus.dataAsOf && (
                <div className="text-[11px] text-emerald-700 mt-1">
                  Data as of {dbStatus.dataAsOf}
                </div>
              )}
            </div>
            <button
              onClick={onValueFromDb}
              disabled={loadingFromDb || (dbStatus.requiresPolicy && !tdxPolicy)}
              className="shrink-0 px-5 py-2.5 bg-emerald-600 text-white rounded-lg text-sm font-semibold hover:bg-emerald-700 disabled:bg-gray-300"
            >
              {loadingFromDb ? 'Running valuation…' : '→ Value from Database'}
            </button>
          </div>
          {dbStatus.requiresPolicy && tdxPolicy && <div className="mt-3 space-y-3 text-sm">
            <p>默认值仅用于条件估值，并非公司事实或自动市场估计。报告期：{dbStatus.reportPeriod}。</p>
            <p>前五年增长取历史季度同比中位数加调整值，并受上下限约束；设置相同上下限可指定固定增长。利润率从当前水平过渡至调整后水平。</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {scenarioFields.map(([key, label]) => <label key={key} className="block">{label}
                <input type="number" step="any" className="block border rounded p-2 w-full" value={tdxPolicy.policy[key] == null ? '' : Number((Number(tdxPolicy.policy[key]) * scale(label)).toPrecision(12))}
                  onChange={event => onPolicy({ ...tdxPolicy, policy: { ...tdxPolicy.policy, [key]: event.target.value === '' ? null : Number(event.target.value) / scale(label) } })} />
              </label>)}
            </div>
            <p>采用财报日股本、账面债务及不转股情景；未分类金融投资暂不计价。附加索偿为0是情景假设，不代表确认不存在。</p>
          </div>}
        </div>
      )}

      {/* Path B — upload a data file from the maintainer. */}
      <div className="bg-slate-50 border border-slate-300 rounded-lg p-4 space-y-3">
        <div>
          <div className="text-xs text-slate-700 uppercase font-semibold mb-1">
            {dbStatus?.inDb ? 'Option B — Upload a data file' : 'Upload a data file'}
          </div>
          <p className="text-sm text-slate-700 leading-relaxed">
            Email the repository maintainer with the ticker above; they'll send back a data file
            you can upload here.
          </p>
        </div>

        <label
          htmlFor="upload-filled-xlsx"
          onDragOver={handleDragOver}
          onDragEnter={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`block border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition ${zoneState}`}
        >
          <input
            ref={fileInputRef}
            id="upload-filled-xlsx"
            type="file"
            accept=".xlsx,.xls"
            onChange={onUploadChange}
            disabled={uploading}
            className="hidden"
          />
          {uploading ? (
            <>
              <div className="text-3xl mb-2">⏳</div>
              <div className="text-sm font-semibold text-blue-900">Parsing + valuing…</div>
              <div className="text-xs text-blue-600 mt-1">This usually takes 2–5 seconds.</div>
            </>
          ) : dragging ? (
            <>
              <div className="text-3xl mb-2">📥</div>
              <div className="text-sm font-semibold text-blue-900">Release to upload</div>
            </>
          ) : (
            <>
              <div className="text-3xl mb-2">📤</div>
              <div className="text-sm font-semibold text-gray-800">
                Click to choose a file
              </div>
              <div className="text-xs text-gray-500 mt-1">
                or drag & drop an <code className="bg-white px-1 rounded">.xlsx</code> file here
              </div>
            </>
          )}
        </label>
      </div>

      <div className="flex justify-between pt-2 border-t border-gray-100">
        <button onClick={onBack} className="text-sm text-gray-500 hover:text-gray-700">
          ← Change company
        </button>
      </div>
    </div>
  );
}
