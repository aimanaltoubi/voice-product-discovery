import React from 'react';

/**
 * Quick refinements. Each submits a short refinement utterance against the
 * ACTIVE session — the backend merges it onto the accumulated constraints, so
 * these no longer need to restate the product type. Nothing here filters the
 * current result list; every click reruns full-catalog retrieval.
 */
export default function RefineActions({ understood, onRefine, disabled = false }) {
  if (!understood?.product_type || !onRefine) return null;

  const budget = understood.budget;
  const features = (understood.qualitative_features || []).map((f) => f.toLowerCase());
  const has = (f) => features.some((x) => x.includes(f));

  const actions = [];

  if (typeof budget === 'number') {
    actions.push({
      label: 'Cheaper',
      query: `Actually keep it under $${Math.max(1, Math.floor(budget * 0.75))}.`,
    });
  } else {
    actions.push({ label: 'Cheaper', query: 'Show me cheaper options.' });
  }

  if (!has('light')) actions.push({ label: 'Lightweight', query: 'Something lightweight too.' });
  if (!has('compact') && !has('small')) {
    actions.push({ label: 'More compact', query: 'Something more compact.' });
  }
  if (!has('wash')) {
    actions.push({ label: 'Machine washable', query: 'Make it machine washable.' });
  }
  if (!has('durable')) actions.push({ label: 'More durable', query: 'Something more durable.' });

  actions.push({
    label: 'Check current prices',
    query: 'What is the current online price and availability of these right now?',
  });

  // Keep the live-price action, drop the middle if we have too many.
  const shown = actions.length > 4
    ? [...actions.slice(0, 3), actions[actions.length - 1]]
    : actions;

  // Heading is supplied by the compact composer section in Home.jsx.
  return (
    <div className="flex flex-wrap gap-2">
        {shown.map((a) => (
          <button
            key={a.label}
            type="button"
            // Disabled while a request is in flight — these each trigger a full
            // pipeline run, so a double-click would fire two searches.
            disabled={disabled}
            onClick={() => onRefine(a.query)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-[13px] font-medium text-slate-700 transition-colors hover:border-slate-400 hover:bg-slate-50 hover:text-slate-900 active:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-slate-300 disabled:hover:bg-white"
          >
            {a.label}
          </button>
        ))}
    </div>
  );
}
