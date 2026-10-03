// Test-only document: actual UI components with synthetic values, never a production route.
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { KnowledgeProvider, KnowledgePanel, Term, useKnowledge } from '../../src/knowledge';
import SpreadsheetCell from '../../src/components/SpreadsheetCell';
import type { ValuationResponse } from '../../src/types/valuation';
import '../../src/index.css';

export function SelectionToggle() {
  const knowledge = useKnowledge();
  return <><label data-knowledge-ignore><input type="checkbox" checked={knowledge?.selectionEnabled ?? false}
    onChange={event => knowledge?.setSelectionEnabled(event.target.checked)} />划词解释</label>
    <output data-testid="knowledge-status" hidden>{knowledge?.index?.status ?? 'loading'}</output></>;
}

export function Harness() {
  const [wacc, setWacc] = useState(0.1);
  const [cell, setCell] = useState('WACC');
  // Only the allowlisted WACC response field is consumed by this fixture.
  const valuation = { inputs: { reporting_currency: 'USD' }, cost_of_capital: { wacc } } as ValuationResponse;
  return <BrowserRouter><KnowledgeProvider valuation={valuation}>
    <div className="flex min-h-screen">
      <main className="min-w-0 flex-1 p-6" data-knowledge-scope>
        <h1>合成词条交互测试</h1>
        <SelectionToggle />
        <p><Term termId="wacc" bindingId="wacc.current">WACC</Term></p>
        <p data-testid="ambiguous">资本</p>
        <p data-testid="alias">Ｃａｐｉｔａｌ ｃｏｓｔ</p>
        <p data-testid="numeric">123.45%</p>
        <p data-testid="long-selection">{'WACC '.repeat(20)}</p>
        <p data-testid="ignored" data-knowledge-ignore>WACC</p>
        <p contentEditable suppressContentEditableWarning data-testid="editable-text">WACC</p>
        <label>普通输入<input aria-label="普通输入" defaultValue="WACC" /></label>
        <table><tbody>
          <tr data-testid="financial-labels"><SpreadsheetCell value="Revenues" type="label" /><SpreadsheetCell value="EBITDA" type="header" /></tr>
          <tr data-testid="ambiguous-label"><SpreadsheetCell value="资本" type="label" /></tr>
          <tr data-testid="explicit-label"><SpreadsheetCell value="Revenues" type="label" termId="wacc" /></tr>
          <tr data-testid="financial-value"><SpreadsheetCell value="Revenues" type="financial" /></tr>
          <tr data-testid="editable-row"><SpreadsheetCell value={cell} type="hypothesis" editable termId="wacc" onChange={setCell} /></tr>
          <tr data-testid="two-cells"><SpreadsheetCell value="WACC" type="label" /><SpreadsheetCell value="资本" type="label" /></tr>
        </tbody></table>
        <output data-testid="committed-cell">{cell}</output>
        <button type="button" onClick={() => setWacc(0.12)}>更新合成估值</button>
        <button type="button" data-testid="focus-anchor">保留焦点</button>
      </main>
      <KnowledgePanel />
    </div>
  </KnowledgeProvider></BrowserRouter>;
}

createRoot(document.getElementById('root')!).render(<Harness />);
