import React, { useState } from 'react';
import { Check, ExternalLink, ChevronDown, ChevronRight, ImageOff } from 'lucide-react';

/**
 * Recommended products.
 *
 * Images come from `row.image` — the Amazon CDN URL stored on that product's own
 * dataset row (Uniq Id -> AMZ2020-*). Nothing is searched for or inferred; if
 * the field is absent or the URL fails to load we show a neutral placeholder
 * rather than a stand-in photo of a different product.
 *
 * Ranking is the backend's: rows arrive in order and rank 1 is the top pick.
 * The frontend never re-sorts.
 */
const fmtPrice = (v) => (typeof v === 'number' ? `$${v.toFixed(2)}` : null);

/** Long Amazon titles are trimmed at a word boundary; full text stays in the
 *  title attribute and the expandable details. The record is never altered. */
function shortTitle(title, max = 88) {
  if (!title || title.length <= max) return title;
  const cut = title.slice(0, max);
  const at = cut.lastIndexOf(' ');
  return `${cut.slice(0, at > 40 ? at : max).replace(/[\s,\-–—|]+$/, '')}…`;
}

function ProductImage({ src, alt }) {
  const [failed, setFailed] = useState(false);
  const base =
    'h-20 w-20 sm:h-24 sm:w-24 shrink-0 rounded-lg border border-slate-200 bg-slate-50';
  if (!src || failed) {
    return (
      <div className={`${base} flex items-center justify-center`} aria-hidden="true">
        <ImageOff className="h-5 w-5 text-slate-300" />
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt || 'Product image'}
      loading="lazy"
      onError={() => setFailed(true)}
      className={`${base} object-contain p-1.5`}
    />
  );
}

function Chip({ children }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 text-[12.5px] leading-none text-emerald-900">
      <Check className="h-3 w-3 shrink-0" strokeWidth={3} aria-hidden="true" />
      {children}
    </span>
  );
}

function ProductCard({ row, rank, isTop, isAlt }) {
  const [open, setOpen] = useState(false);
  const reasons = row.match_reasons || [];
  const { matched_constraints: matched, supported_constraints: supported } = row;
  const showBrand = row.brand && row.brand.toLowerCase() !== 'unknown';
  const showRating = typeof row.rating === 'number';
  const details = row.features || row.ingredients;
  const isLive = Boolean(row.url);

  return (
    <li className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex gap-4">
        <ProductImage src={row.image} alt={row.title} />

        <div className="min-w-0 flex-1">
          {isTop && !isAlt && (
            <span className="mb-1.5 inline-block rounded bg-slate-900 px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide text-white">
              Top pick
            </span>
          )}

          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <h3
              className="text-[15px] font-medium leading-snug text-slate-900"
              title={row.title}
            >
              {shortTitle(row.title)}
            </h3>
            {fmtPrice(row.price) && (
              <span className="shrink-0 text-[17px] font-semibold tabular-nums text-slate-900">
                {fmtPrice(row.price)}
              </span>
            )}
          </div>

          {reasons.length > 0 && (
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {reasons.map((r) => <Chip key={r}>{r}</Chip>)}
            </div>
          )}

          {typeof matched === 'number' && supported > 0 && (
            <p className="mt-2 text-[12.5px] text-slate-500">
              Matches{' '}
              <span className="font-medium text-slate-700">{matched} of {supported}</span>{' '}
              supported criteria
            </p>
          )}

          {/* provenance */}
          <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-slate-500">
            {isLive ? (
              <>
                <span className="font-medium text-teal-700">Live web</span>
                <a
                  href={row.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-teal-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 rounded"
                >
                  <ExternalLink className="h-3 w-3" aria-hidden="true" />
                  Open source
                </a>
              </>
            ) : (
              <>
                <span className="font-medium text-slate-600">Amazon 2020 catalog</span>
                {row.doc_id && <span className="font-mono">{row.doc_id}</span>}
              </>
            )}
            {showBrand && <span>· {row.brand}</span>}
            {showRating && <span>· {row.rating.toFixed(1)} ★</span>}
          </div>

          {details && (
            <>
              <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                className="mt-2 flex items-center gap-1 rounded text-[12.5px] text-slate-500 transition-colors hover:text-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
              >
                {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                Details
              </button>
              {open && (
                <div className="mt-2 space-y-2">
                  <p className="text-[13px] font-medium leading-snug text-slate-700 break-words">
                    {row.title}
                  </p>
                  <p className="text-[13px] leading-relaxed text-slate-600 break-words">
                    {details}
                  </p>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </li>
  );
}

export default function ProductResults({ rows, variant = 'recommended' }) {
  if (!rows?.length) return null;
  const isAlt = variant === 'alternatives';
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold text-slate-900">
        {isAlt ? 'Closest alternatives' : 'Recommended for you'}
      </h2>
      {isAlt && (
        <p className="-mt-1 text-[13px] text-slate-500">
          These do <span className="font-medium">not</span> meet your stated criteria — shown for reference only.
        </p>
      )}
      <ol className="space-y-3">
        {rows.map((r, i) => (
          <ProductCard
            key={r.doc_id || i}
            row={r}
            rank={i + 1}
            isTop={i === 0}
            isAlt={isAlt}
          />
        ))}
      </ol>
    </section>
  );
}
