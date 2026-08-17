import React, { useState } from 'react';
import {
  Check, ExternalLink, ChevronDown, ChevronRight, ImageOff, Minus,
} from 'lucide-react';

const fmtPrice = (v) => (typeof v === 'number' ? `$${v.toFixed(2)}` : null);

function meaningful(value) {
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  if (!text || /^(?:unknown|n\/?a|none|null|nan|-+)$/i.test(text)) return null;
  return text;
}

function isLiveRow(row) {
  return row.source === 'web' || String(row.doc_id || '').startsWith('web-');
}

function shortTitle(title, max = 96) {
  if (!title || title.length <= max) return title;
  const cut = title.slice(0, max);
  const at = cut.lastIndexOf(' ');
  return `${cut.slice(0, at > 40 ? at : max).replace(/[\s,\-–—|]+$/, '')}…`;
}

function ProductImage({ src, alt }) {
  const [failed, setFailed] = useState(false);
  const base = 'h-24 w-24 shrink-0 rounded-xl border border-slate-200 bg-slate-50 sm:h-28 sm:w-28';
  if (!src || failed) {
    return (
      <div className={`${base} flex items-center justify-center`} aria-label="Product image unavailable">
        <ImageOff className="h-5 w-5 text-slate-300" aria-hidden="true" />
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt || 'Product image'}
      loading="lazy"
      onError={() => setFailed(true)}
      className={`${base} object-contain p-2`}
    />
  );
}

function Chip({ children }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 text-[12px] leading-none text-emerald-900">
      <Check className="h-3 w-3 shrink-0" strokeWidth={3} aria-hidden="true" />
      {children}
    </span>
  );
}

function ExpandableText({ text, limit = 300 }) {
  const [expanded, setExpanded] = useState(false);
  const value = meaningful(text);
  if (!value) return null;
  const clipped = value.length > limit;
  const shown = !clipped || expanded
    ? value
    : `${value.slice(0, limit).replace(/[\s,;|]+$/, '')}…`;
  return (
    <div>
      <p className="whitespace-pre-line break-words text-[12.5px] leading-relaxed text-slate-600">
        {shown}
      </p>
      {clipped && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-1 rounded text-[12px] font-medium text-blue-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  );
}

function DetailSection({ title, children }) {
  return (
    <section className="border-t border-slate-100 pt-3 first:border-t-0 first:pt-0">
      <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </h4>
      {children}
    </section>
  );
}

function DetailList({ entries }) {
  const present = entries.filter(([, value]) => meaningful(value));
  if (!present.length) return null;
  return (
    <dl className="grid gap-x-5 gap-y-2 sm:grid-cols-2">
      {present.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-[11px] text-slate-400">{label}</dt>
          <dd className="break-words text-[12.5px] text-slate-700">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function unverifiedFeatures(row, requested = []) {
  const { matched_constraints: matched, supported_constraints: supported } = row;
  if (typeof matched !== 'number' || typeof supported !== 'number') return [];
  const gap = supported - matched;
  if (gap <= 0) return [];
  const chips = (row.match_reasons || []).join(' ').toLowerCase();
  const words = (s) => s.toLowerCase().split(/[^a-z]+/).filter((w) => w.length >= 4);
  const missing = requested.filter((f) => {
    const ws = words(f);
    return ws.length > 0 && !ws.some((w) => chips.includes(w.slice(0, Math.max(4, w.length - 2))));
  });
  return missing.slice(0, gap);
}

function ProductCard({ row, rank, isTop, isAlt, requested, liveMatch }) {
  const [open, setOpen] = useState(false);
  const reasons = row.match_reasons || [];
  const unverified = unverifiedFeatures(row, requested);
  const { matched_constraints: matched, supported_constraints: supported } = row;
  const showBrand = meaningful(row.brand);
  const showRating = typeof row.rating === 'number';
  const isLive = isLiveRow(row);
  const hardUnverified = row.unverified_hard || [];
  const productInfo = [
    ['Category', row.category],
    ['Model number', row.model_number],
    ['Catalog record', !isLive ? row.doc_id : null],
  ];
  const logistics = [
    ['Product dimensions', row.product_dimensions],
    ['Shipping weight', row.shipping_weight],
  ];
  const hasProductInfo = productInfo.some(([, v]) => meaningful(v));
  const hasLogistics = logistics.some(([, v]) => meaningful(v));
  const hasDescription = meaningful(row.features) || meaningful(row.ingredients);
  const hasSpecs = meaningful(row.specification) || meaningful(row.technical_details);
  const hasDetails = hasProductInfo || hasLogistics || hasDescription || hasSpecs;

  return (
    <li className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
      <div className="flex flex-col gap-4 sm:flex-row">
        <ProductImage src={row.image} alt={row.title} />

        <div className="min-w-0 flex-1">
          {!isAlt && (
            isTop ? (
              isLive && (hardUnverified.length > 0 || (!row.price_verified && reasons.length === 0)) ? (
                <span className="mb-1.5 inline-block rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide text-blue-800">
                  Online option
                </span>
              ) : (
                <span className="mb-1.5 inline-block rounded bg-slate-900 px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide text-white">
                  Top pick
                </span>
              )
            ) : (
              <span className="mb-1.5 block text-[10.5px] font-medium uppercase tracking-wide text-slate-400">
                Option {rank}
              </span>
            )
          )}

          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
            <h3 className="max-w-2xl text-[15px] font-semibold leading-snug text-slate-900" title={row.title}>
              {shortTitle(row.title)}
            </h3>
            {fmtPrice(row.price) ? (
              <span className="shrink-0 text-[18px] font-semibold tabular-nums text-slate-900">
                {fmtPrice(row.price)}
              </span>
            ) : (
              <span className="shrink-0 text-[12px] text-slate-500">
                {isLive ? 'Price not verified' : 'Price not available'}
              </span>
            )}
          </div>

          {(showBrand || meaningful(row.model_number) || meaningful(row.product_dimensions) || meaningful(row.shipping_weight)) && (
            <p className="mt-1.5 text-[12px] leading-relaxed text-slate-500">
              {[
                showBrand,
                meaningful(row.model_number) && `Model ${row.model_number}`,
                meaningful(row.product_dimensions),
                meaningful(row.shipping_weight) && `${row.shipping_weight} shipping weight`,
              ].filter(Boolean).join(' · ')}
            </p>
          )}

          {liveMatch && (
            <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-blue-700">
              <span className="font-medium">Live checked</span>
              <span>·</span>
              <span>
                {typeof liveMatch.web_price === 'number'
                  ? `Current price ${fmtPrice(liveMatch.web_price)}`
                  : 'Current price not shown by source'}
              </span>
              {liveMatch.web_url && (
                <a
                  href={liveMatch.web_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 rounded hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                >
                  <ExternalLink className="h-3 w-3" aria-hidden="true" />
                  Open live source
                </a>
              )}
            </div>
          )}

          {(reasons.length > 0 || unverified.length > 0 || hardUnverified.length > 0) && (
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              {reasons.map((r) => <Chip key={r}>{r}</Chip>)}
              {hardUnverified.map((u) => (
                <span
                  key={`hard-${u}`}
                  title="Pickly could not verify this must-have from the live listing"
                  className="inline-flex items-center gap-1 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[12px] leading-none text-amber-900"
                >
                  <Minus className="h-3 w-3 shrink-0" aria-hidden="true" />
                  {u} not verified
                </span>
              ))}
              {unverified.map((u) => (
                <span
                  key={u}
                  title="The product listing does not mention this, so it is not claimed"
                  className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[12px] leading-none text-slate-500"
                >
                  <Minus className="h-3 w-3 shrink-0" aria-hidden="true" />
                  {u} not verified
                </span>
              ))}
            </div>
          )}

          {typeof matched === 'number' && supported > 0 && (
            <p className="mt-2.5 text-[12px] text-slate-500">
              Matches <span className="font-medium text-slate-700">{matched} of {supported}</span> preferences
            </p>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-slate-500">
            {isLive ? (
              <>
                <span className="rounded bg-blue-50 px-1.5 py-0.5 font-medium text-blue-700">Live web</span>
                {meaningful(row.retailer) && <span>{row.retailer}</span>}
                <span>{meaningful(row.availability) || 'Availability not verified'}</span>
                {row.url && (
                  <a
                    href={row.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 rounded font-medium text-blue-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                  >
                    <ExternalLink className="h-3 w-3" aria-hidden="true" />
                    View at retailer →
                  </a>
                )}
              </>
            ) : (
              <>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 font-medium text-slate-600">Amazon 2020 dataset</span>
                {row.doc_id && <span className="font-mono text-[11px]">{row.doc_id}</span>}
                {row.product_url && (
                  <a
                    href={row.product_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 rounded font-medium text-blue-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                  >
                    <ExternalLink className="h-3 w-3" aria-hidden="true" />
                    View on Amazon →
                  </a>
                )}
              </>
            )}
            {showRating && <span>· {row.rating.toFixed(1)} ★</span>}
          </div>

          {hasDetails && (
            <>
              <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                className="mt-3 flex items-center gap-1 rounded text-[12.5px] font-medium text-slate-600 hover:text-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
              >
                {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                Details
              </button>
              {open && (
                <div className="mt-3 space-y-3 rounded-xl bg-slate-50 p-3.5">
                  {hasProductInfo && (
                    <DetailSection title="Product information">
                      <DetailList entries={productInfo} />
                    </DetailSection>
                  )}
                  {hasLogistics && (
                    <DetailSection title="Size & logistics">
                      <DetailList entries={logistics} />
                    </DetailSection>
                  )}
                  {hasDescription && (
                    <DetailSection title="Description">
                      <div className="space-y-2">
                        <ExpandableText text={row.features} />
                        <ExpandableText text={row.ingredients} />
                      </div>
                    </DetailSection>
                  )}
                  {hasSpecs && (
                    <DetailSection title="Specifications">
                      <div className="space-y-2">
                        <ExpandableText text={row.specification} />
                        {row.technical_details !== row.specification && (
                          <ExpandableText text={row.technical_details} />
                        )}
                      </div>
                    </DetailSection>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </li>
  );
}

export default function ProductResults({
  rows, variant = 'recommended', requested = [], reconciliation,
}) {
  if (!rows?.length) return null;
  const isAlt = variant === 'alternatives';
  const isRelated = variant === 'related';
  const live = rows.filter(isLiveRow).length;
  const priv = rows.length - live;
  const item = (n, noun = 'match') => `${n} ${noun}${n === 1 ? '' : 'es'}`;
  const summary = isRelated
    ? `${item(rows.length, 'related product')} found online`
    : live && priv
      ? `${item(rows.length, 'recommendation')} · Amazon 2020 dataset + live web`
      : live
        ? `${item(live)} found online`
        : `${item(priv)} from the Amazon 2020 dataset, best first`;

  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-[20px] font-semibold tracking-tight text-slate-900">
          {isAlt ? 'Closest alternatives' : isRelated ? 'Related options found online' : 'Recommended for you'}
        </h2>
        <p className="mt-0.5 text-[13px] text-slate-500">
          {isAlt ? 'These do not meet your stated criteria — shown for reference only.' : summary}
        </p>
      </div>
      <ol className="space-y-3">
        {rows.map((r, i) => (
          <ProductCard
            key={r.doc_id || i}
            row={r}
            rank={i + 1}
            isTop={i === 0}
            isAlt={isAlt || isRelated}
            requested={isAlt || isRelated ? [] : requested}
            liveMatch={reconciliation?.matches?.[r.doc_id]}
          />
        ))}
      </ol>
    </section>
  );
}
