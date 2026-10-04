// Only rendered by the browser regression, never mounted as an application route.
import { createRoot } from 'react-dom/client';
import SummarySheet from '../../src/pages/SummarySheet';
import type { ValuationResponse } from '../../src/types/valuation';
import '../../src/index.css';

fetch('/api/synthetic-summary').then(response => response.json()).then((data: ValuationResponse) => {
  createRoot(document.getElementById('root')!).render(<SummarySheet data={data} />);
});
