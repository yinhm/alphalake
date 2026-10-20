// Only rendered by the browser regression, never mounted as an application route.
import { createRoot } from 'react-dom/client';
import SummarySheet from '../../src/pages/SummarySheet';
import InputSheet from '../../src/pages/InputSheet';
import StoriesToNumbers from '../../src/pages/StoriesToNumbers';
import Diagnostics from '../../src/pages/Diagnostics';
import TrailingTwelveMonth from '../../src/pages/TrailingTwelveMonth';
import FailureRate from '../../src/pages/FailureRate';
import OptionValue from '../../src/pages/OptionValue';
import RelativeValuation from '../../src/pages/RelativeValuation';
import SpreadsheetCell from '../../src/components/SpreadsheetCell';
import ValuationOutput from '../../src/pages/ValuationOutput';
import { useState } from 'react';
import type { PatchValue } from '../../src/api/client';
import type { ValuationResponse } from '../../src/types/valuation';
import '../../src/index.css';

function Harness({ data }: { data: ValuationResponse }) {
  const [patch, setPatch] = useState<Record<string, PatchValue>>({});
  const sheet = new URLSearchParams(location.search).get('sheet');
  return <>
    {sheet === 'failure' ? <FailureRate data={data} onPatch={(path, value) => setPatch({ [path]: value })} />
      : sheet === 'options' ? <OptionValue data={data} />
      : sheet === 'relative' ? <RelativeValuation data={data} />
      : sheet === 'cell' ? <table><tbody><tr><SpreadsheetCell value={.5} type="financial" /><SpreadsheetCell value={.5} type="reference" /><SpreadsheetCell value={.005} format="percent" type="calc" /></tr></tbody></table>
      : sheet === 'input' ? <InputSheet data={data} onUpdate={(path, value) => setPatch({ [path]: value })} />
      : sheet === 'stories' ? <StoriesToNumbers data={data} onPatchMany={setPatch} />
        : sheet === 'diagnostics' ? <Diagnostics data={data} />
          : sheet === 'ltm' ? <TrailingTwelveMonth data={data} />
            : sheet === 'output' ? <ValuationOutput data={data} onPatch={(path, value) => setPatch({ [path]: value })} />
        : <SummarySheet data={data} />}
    <output data-testid="patch">{JSON.stringify(patch)}</output>
  </>;
}

fetch('/api/synthetic-summary').then(response => response.json()).then((data: ValuationResponse) => {
  createRoot(document.getElementById('root')!).render(<Harness data={data} />);
});
