import { NavLink } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { adminWhoami } from '../api/client';
import { Menu, PanelLeftClose, BookOpen } from 'lucide-react';

// Order reflects the valuation workflow: inputs → adjustments & WACC → outputs → cross-checks.
const NAV = [
  // Inputs & data prep
  { to: '/',                  label: '1. Input Sheet' },
  { to: '/ttm',               label: '2. Trailing 12 Month' },
  // Adjustments & Cost of Capital
  { to: '/rd',                label: '3. R&D Converter' },
  { to: '/leases',            label: '4. Lease Converter' },
  { to: '/rating',            label: '5. Synthetic Rating' },
  { to: '/wacc',              label: '6. Cost of Capital' },
  { to: '/failure',           label: '7. Failure Rate' },
  { to: '/options',           label: '8. Option Value' },
  // Valuation output
  { to: '/valuation-output',  label: '9. Valuation Output' },
  { to: '/summary',           label: '10. Summary Sheet' },
  { to: '/stories',           label: '11. Stories to Numbers' },
  { to: '/picture',           label: '12. Valuation as Picture' },
  // Cross-checks & references
  { to: '/relative',          label: '13. Relative Valuation' },
  { to: '/diagnostics',       label: '14. Diagnostics' },
  { to: '/answers',           label: '15. Answer Keys' },
];

export default function Sidebar() {
  const [expanded, setExpanded] = useState(() => typeof window === 'undefined' || window.matchMedia('(min-width: 1024px)').matches);
  // The admin link only renders when the server has configured
  // AD_CC_ADMIN_TOKEN AND the browser has a stored token that matches.
  // Plain users never see the link; cloners of the open-source repo
  // start with admin disabled until they set their own token.
  const [adminVisible, setAdminVisible] = useState(false);
  useEffect(() => {
    adminWhoami().then((w) => setAdminVisible(w.configured)).catch(() => setAdminVisible(false));
  }, []);

  return (
    <aside data-knowledge-ignore className={`${expanded ? 'lg:w-56' : 'lg:w-14'} w-full shrink-0 border-r border-gray-200 bg-gray-50 lg:min-h-screen p-3`}>
      <div className="flex items-center justify-between gap-1 mb-4">
        {expanded && <h1 className="text-base font-bold px-2">Valuation Sheets</h1>}
        <button type="button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}
          aria-controls="valuation-navigation" aria-label={expanded ? '收起导航' : '展开导航'}
          className="p-1.5 rounded hover:bg-gray-200 focus-visible:outline-2 focus-visible:outline-blue-600">
          {expanded ? <PanelLeftClose size={18} /> : <Menu size={18} />}
        </button>
      </div>
      <nav id="valuation-navigation" aria-label="估值页面" className={expanded ? 'flex flex-col gap-0.5' : 'hidden'}>
        {NAV.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `px-3 py-1.5 rounded text-xs transition-colors ${
                isActive
                  ? 'bg-blue-100 text-blue-700 font-medium'
                  : 'text-gray-600 hover:bg-gray-100'
              }`
            }
          >
            {label}
          </NavLink>
        ))}
        <NavLink to="/knowledge" className={({ isActive }) =>
          `mt-3 px-3 py-2 rounded text-xs flex items-center gap-2 ${isActive ? 'bg-blue-100 text-blue-700 font-medium' : 'text-gray-600 hover:bg-gray-100'}`}>
          <BookOpen size={15} /> 估值知识库
        </NavLink>
        {adminVisible && (
          <>
            <div className="mt-3 mb-1 px-2 text-[10px] uppercase text-slate-400 tracking-wide">Admin</div>
            <NavLink
              to="/admin"
              className={({ isActive }) =>
                `px-3 py-1.5 rounded text-xs transition-colors ${
                  isActive
                    ? 'bg-amber-100 text-amber-800 font-medium'
                    : 'text-gray-600 hover:bg-gray-100'
                }`
              }
            >
              ⚙ Data Sources
            </NavLink>
          </>
        )}
      </nav>
    </aside>
  );
}
