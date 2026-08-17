import React from 'react';
import { Globe, ArrowUpRight } from 'lucide-react';

/**
 * web.search returned pages, but none carried enough product-level evidence to
 * recommend. Showing a retailer's "check a price" landing page as a Top pick is
 * worse than admitting we found nothing, so this state exists instead of a
 * generic-page fallback.
 */
export default function NoLiveMatchState({ liveUnverified, onRaiseBudget, budget, onReset }) {
  if (!liveUnverified) return null;
  const { checked = 0, rejected = [] } = liveUnverified;

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="flex items-start gap-3">
        <Globe className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[17px] font-semibold tracking-tight text-slate-900">
            No verified online match
          </h2>
          <p className="mt-1 text-[14px] text-slate-700">
            I found {checked > 0 ? `${checked} related page${checked === 1 ? '' : 's'}` : 'related pages'}{' '}
            online, but not enough product-level information to recommend one
            confidently.
          </p>

          {rejected.length > 0 && (
            <details className="mt-3">
              <summary className="cursor-pointer text-[12.5px] text-slate-500 hover:text-slate-800">
                What was found and why it was skipped
              </summary>
              <ul className="mt-2 space-y-1.5 border-l border-slate-200 pl-3">
                {rejected.slice(0, 5).map((r, i) => (
                  <li key={i} className="text-[12.5px] leading-snug">
                    <a
                      href={r.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-slate-700 underline underline-offset-2 hover:text-slate-900"
                    >
                      {r.title || r.url}
                    </a>
                    <span className="text-slate-400"> — {r.reason}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            {typeof budget === 'number' && onRaiseBudget && (
              <button
                type="button"
                onClick={() => onRaiseBudget(Math.ceil(budget * 2))}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-[13px] font-medium text-slate-800 transition-colors hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
              >
                <ArrowUpRight className="h-3.5 w-3.5" />
                Raise budget
              </button>
            )}
            {onReset && (
              <button
                type="button"
                onClick={onReset}
                className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-[13px] font-medium text-slate-800 transition-colors hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
              >
                Start new search
              </button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
