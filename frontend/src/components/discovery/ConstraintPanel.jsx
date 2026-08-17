import React from 'react';

/** Compact Router read-out. The editable, accumulated chip model lives in
 * YourSearch; this panel only records what the latest routing pass understood. */
const money = (v) =>
  Number.isInteger(v) ? `$${v.toLocaleString()}` : `$${v.toFixed(2)}`;

export default function ConstraintPanel({ understood, topK, needsLive }) {
  if (!understood) return null;

  const items = [
    ['Product', understood.product_type],
    ['Budget', typeof understood.budget === 'number' ? `≤ ${money(understood.budget)}` : null],
    ['Size', understood.size],
    ['For', understood.audience],
    ['Use', understood.use_case],
    ['Color', understood.color],
    ['Material', understood.material],
    ['Results', topK ? `up to ${topK}` : null],
    ['Information', needsLive ? 'current / live' : 'private catalog'],
  ].filter(([, value]) => value);

  if (!items.length) return null;

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">What I understood</h2>
      <dl className="mt-2.5 grid grid-cols-2 gap-x-4 gap-y-2">
        {items.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-[10.5px] font-medium uppercase tracking-wide text-slate-400">
              {label}
            </dt>
            <dd className="truncate text-[12.5px] text-slate-700" title={String(value)}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
      {(understood.qualitative_features || []).length > 0 && (
        <p className="mt-2.5 border-t border-slate-100 pt-2 text-[12px] leading-relaxed text-slate-500">
          Preferences: {(understood.qualitative_features || []).join(', ')}
        </p>
      )}
    </section>
  );
}
