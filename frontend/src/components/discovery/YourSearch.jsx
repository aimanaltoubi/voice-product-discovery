import React, { useState } from 'react';
import { X, RotateCcw, ChevronDown, ChevronRight } from 'lucide-react';

/**
 * "Your search" — the accumulated session, not the latest utterance.
 *
 * Structured constraints (product type, size, budget, audience, use case) are
 * shown as plain chips. Soft preferences are removable: removing one reruns the
 * whole search with the remaining constraints. Product type is deliberately not
 * removable — dropping it is a new search, which is what the reset control is
 * for.
 */
const money = (v) =>
  Number.isInteger(v) ? `$${v.toLocaleString()}` : `$${v.toFixed(2)}`;

function Chip({ children, onRemove, label }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 py-1 pl-2 pr-1 text-[13px] leading-none text-slate-800">
      {children}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${label}`}
          className="ml-0.5 rounded p-0.5 text-slate-400 transition-colors hover:bg-slate-200 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
        >
          <X className="h-3 w-3" />
        </button>
      )}
    </span>
  );
}

export default function YourSearch({ session, onRemoveFeature, onReset }) {
  const [showHistory, setShowHistory] = useState(false);
  const c = session?.constraints;
  if (!c?.product_type) return null;

  const features = c.qualitative_features || [];
  const history = session.history || [];

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">Your search</h2>
        <button
          type="button"
          onClick={onReset}
          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-[12.5px] font-medium text-slate-700 transition-colors hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
        >
          <RotateCcw className="h-3 w-3" />
          Start new search
        </button>
      </div>

      <div className="flex flex-wrap gap-1.5">
        <Chip>{c.product_type}</Chip>
        {c.size && <Chip>{c.size}</Chip>}
        {typeof c.budget === 'number' && <Chip>≤ {money(c.budget)}</Chip>}
        {c.audience && <Chip>{c.audience}</Chip>}
        {c.use_case && <Chip>{c.use_case}</Chip>}
        {c.brand && <Chip>{c.brand}</Chip>}
        {c.material && <Chip>{c.material}</Chip>}
        {features.map((f) => (
          <Chip key={f} label={f} onRemove={() => onRemoveFeature(f)}>
            {f}
          </Chip>
        ))}
      </div>

      {history.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setShowHistory((v) => !v)}
            aria-expanded={showHistory}
            className="mt-3 flex items-center gap-1 rounded text-[12.5px] text-slate-500 transition-colors hover:text-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          >
            {showHistory ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            Refined {history.length} time{history.length === 1 ? '' : 's'}
          </button>
          {showHistory && (
            <ol className="mt-2 space-y-1 border-l border-slate-200 pl-3">
              {history.map((h, i) => (
                <li key={i} className="text-[12.5px] text-slate-500">
                  <span className="text-slate-700">{h.utterance}</span>
                  {h.changes?.length > 0 && (
                    <span className="text-slate-400"> — {h.changes.join(' · ')}</span>
                  )}
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  );
}
