import React, { useState } from 'react';
import { Check, Minus, ChevronRight } from 'lucide-react';

/**
 * Collapsed side-by-side comparison.
 *
 * Every cell is derived from data already grounded elsewhere: the structured
 * price from Chroma metadata, and the evidence chips that graph/nodes.py
 * :match_evidence produced from the product's own text. The LLM is never asked
 * to fill this table, so it cannot invent a value.
 *
 * Rows the dataset cannot support (rating, brand) are omitted rather than
 * rendered empty — an "Unknown" column teaches the reader nothing.
 */
const fmt = (v) => (typeof v === 'number' ? `$${v.toFixed(2)}` : '—');

function Cell({ children, muted }) {
  return (
    <td className={`border-t border-slate-200 px-3 py-2 align-middle text-[13px] ${
      muted ? 'text-slate-400' : 'text-slate-800'
    }`}>
      {children}
    </td>
  );
}

export default function ComparisonTable({ rows, reconciliation }) {
  const [open, setOpen] = useState(false);
  if (!rows?.length || rows.length < 2) return null;
  const compared = rows.slice(0, 3);

  // Union of every evidence chip across the compared products, in first-seen
  // order. A product gets a ✓ only if that exact chip is on its own record.
  const criteria = [];
  const seen = new Set();
  compared.forEach((r) => (r.match_reasons || []).forEach((c) => {
    const k = c.toLowerCase();
    if (!seen.has(k)) { seen.add(k); criteria.push(c); }
  }));

  const anyBrand = compared.some((r) => r.brand && r.brand.toLowerCase() !== 'unknown');
  const anyRating = compared.some((r) => typeof r.rating === 'number');
  const matches = reconciliation?.matches || {};
  const anyLive = compared.some((r) => matches[r.doc_id]);

  const shortName = (t, i) =>
    i === 0 ? 'Top pick' : `Option ${i + 1}`;

  return (
    <section className="rounded-xl border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 rounded-xl px-5 py-3.5 text-left transition-colors hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
      >
        <span className="text-sm font-semibold text-slate-900">
          Compare {compared.length === 3 ? 'top 3' : `${compared.length} products`}
        </span>
        <ChevronRight
          className={`h-4 w-4 shrink-0 text-slate-400 transition-transform duration-200 ${open ? 'rotate-90' : ''}`}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="overflow-x-auto px-5 pb-5">
          <table className="w-full min-w-[520px] border-collapse text-left">
            <caption className="sr-only">
              Side-by-side comparison of the recommended products
            </caption>
            <thead>
              <tr>
                <th scope="col" className="px-3 py-2 text-[12px] font-medium uppercase tracking-wide text-slate-500">
                  Criteria
                </th>
                {compared.map((r, i) => (
                  <th
                    key={r.doc_id || i}
                    scope="col"
                    title={r.title}
                    className="px-3 py-2 text-[12px] font-semibold text-slate-900"
                  >
                    {shortName(r.title, i)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row" className="border-t border-slate-200 px-3 py-2 text-[13px] font-medium text-slate-600">
                  Price
                </th>
                {compared.map((r, i) => (
                  <Cell key={i}><span className="font-semibold tabular-nums">{fmt(r.price)}</span></Cell>
                ))}
              </tr>

              {anyLive && (
                <>
                  <tr>
                    <th scope="row" className="border-t border-slate-200 px-3 py-2 text-[13px] font-medium text-slate-600">
                      Live price
                    </th>
                    {compared.map((r, i) => {
                      const m = matches[r.doc_id];
                      return (
                        <Cell key={i} muted={!m || typeof m.web_price !== 'number'}>
                          {m && typeof m.web_price === 'number'
                            ? <span className="tabular-nums">{fmt(m.web_price)}</span>
                            : 'Not verified'}
                        </Cell>
                      );
                    })}
                  </tr>
                  <tr>
                    <th scope="row" className="border-t border-slate-200 px-3 py-2 text-[13px] font-medium text-slate-600">
                      Live availability
                    </th>
                    {compared.map((r, i) => {
                      const m = matches[r.doc_id];
                      return (
                        <Cell key={i} muted={!m?.availability}>
                          {m?.availability || 'Not verified'}
                        </Cell>
                      );
                    })}
                  </tr>
                </>
              )}

              {criteria.map((c) => (
                <tr key={c}>
                  <th scope="row" className="border-t border-slate-200 px-3 py-2 text-[13px] font-medium text-slate-600">
                    {c}
                  </th>
                  {compared.map((r, i) => {
                    const has = (r.match_reasons || []).some(
                      (x) => x.toLowerCase() === c.toLowerCase()
                    );
                    return (
                      <Cell key={i} muted={!has}>
                        {has
                          ? <Check className="h-3.5 w-3.5 text-emerald-700" strokeWidth={3} aria-label="Yes" />
                          : <Minus className="h-3.5 w-3.5 text-slate-300" aria-label="Not supported by evidence" />}
                      </Cell>
                    );
                  })}
                </tr>
              ))}

              {anyRating && (
                <tr>
                  <th scope="row" className="border-t border-slate-200 px-3 py-2 text-[13px] font-medium text-slate-600">
                    Rating
                  </th>
                  {compared.map((r, i) => (
                    <Cell key={i} muted={typeof r.rating !== 'number'}>
                      {typeof r.rating === 'number' ? `${r.rating.toFixed(1)} ★` : '—'}
                    </Cell>
                  ))}
                </tr>
              )}

              {anyBrand && (
                <tr>
                  <th scope="row" className="border-t border-slate-200 px-3 py-2 text-[13px] font-medium text-slate-600">
                    Brand
                  </th>
                  {compared.map((r, i) => {
                    const b = r.brand && r.brand.toLowerCase() !== 'unknown' ? r.brand : null;
                    return <Cell key={i} muted={!b}>{b || '—'}</Cell>;
                  })}
                </tr>
              )}

              <tr>
                <th scope="row" className="border-t border-slate-200 px-3 py-2 text-[13px] font-medium text-slate-600">
                  Match
                </th>
                {compared.map((r, i) => (
                  <Cell key={i}>
                    {typeof r.matched_constraints === 'number' && r.supported_constraints
                      ? `${r.matched_constraints}/${r.supported_constraints}`
                      : '—'}
                  </Cell>
                ))}
              </tr>

              <tr>
                <th scope="row" className="border-t border-slate-200 px-3 py-2 text-[13px] font-medium text-slate-600">
                  Source
                </th>
                {compared.map((r, i) => (
                  <Cell key={i}>
                    {r.source === 'web' || String(r.doc_id || '').startsWith('web-')
                      ? 'Live web'
                      : 'Amazon 2020'}
                  </Cell>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
