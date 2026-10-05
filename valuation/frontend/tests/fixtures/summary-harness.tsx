// Only rendered by the browser regression, never mounted as an application route.
import { createRoot } from 'react-dom/client';
import SummarySheet from '../../src/pages/SummarySheet';
import InputSheet from '../../src/pages/InputSheet';
import StoriesToNumbers from '../../src/pages/StoriesToNumbers';
import Diagnostics from '../../src/pages/Diagnostics';
import TrailingTwelveMonth from '../../src/pages/TrailingTwelveMonth';
import ValuationOutput from '../../src/pages/ValuationOutput';
import { useState } from 'react';
import type { PatchValue } from '../../src/api/client';
import type { ValuationResponse } from '../../src/types/valuation';
import '../../src/index.css';

function Harness({ data }: { data: ValuationResponse }) {
  const [patch, setPatch] = useState<Record<string, PatchValue>>({});
  const sheet = new URLSearchParams(location.search).get('sheet');
  return <>
    {sheet === 'input' ? <InputSheet data={data} onUpdate={(path, value) => setPatch({ [path]: value })} />
      : sheet === 'stories' ? <StoriesToNumbers data={data} onPatchMany={setPatch} />
        : sheet === 'diagnostics' ? <Diagnostics data={data} />
          : sheet === 'ltm' ? <TrailingTwelveMonth data={data} />
            : sheet === 'output' ? <ValuationOutput data={data} />
        : <SummarySheet data={data} />}
    <output data-testid="patch">{JSON.stringify(patch)}</output>
  </>;
}

fetch('/api/synthetic-summary').then(response => response.json()).then((data: ValuationResponse) => {
  createRoot(document.getElementById('root')!).render(<Harness data={data} />);
});
