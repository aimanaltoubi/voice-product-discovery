import React from 'react';

/**
 * Quick refinements. Each one just submits a refined natural-language request
 * through the normal pipeline — no separate recommendation engine, and no
 * pretend conversational memory: the product type is written into the query
 * text explicitly so the request stands on its own.
 */
export default function RefineActions({ understood, onRefine }) {
  if (!understood || !onRefine) return null;

  const product = understood.product_type;
  if (!product) return null;

  const budget = understood.budget;
  const audience = (understood.audience || '').toLowerCase();
  const isChild = /child|kid|toddler|baby|infant|teen/.test(audience);

  const actions = [];

  if (typeof budget === 'number') {
    actions.push({
      label: 'Cheaper',
      query: `Recommend a ${product} under $${Math.max(1, Math.floor(budget / 2))}.`,
    });
  } else {
    actions.push({ label: 'Budget options', query: `Recommend an affordable ${product} under $25.` });
  }

  if (!understood.use_case) {
    actions.push({ label: 'For travel', query: `Recommend a ${product} for travel.` });
  }

  if (!audience) {
    actions.push({ label: 'For adults', query: `Recommend a ${product} for an adult.` });
    actions.push({ label: 'For kids', query: `Recommend a ${product} for a child.` });
  } else if (isChild) {
    actions.push({ label: 'For adults', query: `Recommend a ${product} for an adult.` });
  } else {
    actions.push({ label: 'For kids', query: `Recommend a ${product} for a child.` });
  }

  actions.push({
    label: 'Check current prices',
    query: `What is the current online price and availability of ${product}${
      typeof budget === 'number' ? ` under $${budget}` : ''
    } right now?`,
  });

  return (
    <section className="space-y-2">
      <h2 className="text-[12px] font-medium uppercase tracking-wide text-slate-500">
        Refine
      </h2>
      <div className="flex flex-wrap gap-2">
        {actions.slice(0, 4).map((a) => (
          <button
            key={a.label}
            type="button"
            onClick={() => onRefine(a.query)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-[13px] font-medium text-slate-700 transition-colors hover:bg-slate-50 hover:text-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          >
            {a.label}
          </button>
        ))}
      </div>
    </section>
  );
}
