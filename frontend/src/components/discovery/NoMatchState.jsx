import React from 'react';
import { SearchX, ArrowUpRight, Globe } from 'lucide-react';

/**
 * Shown when a HARD constraint eliminated every catalog product.
 *
 * Both figures are grounded: `requested_max_price` is what the router parsed
 * from the user, `closest_available_price` is a real indexed catalog price.
 * Nothing here is presented as a qualifying match.
 */
const money = (v) =>
  typeof v !== 'number' ? null : Number.isInteger(v) ? `$${v.toLocaleString()}` : `$${v.toFixed(2)}`;

export default function NoMatchState({ noMatch, onRaiseBudget, onSearchOnline }) {
  if (!noMatch) return null;
  const requested = money(noMatch.requested_max_price);
  const closest = money(noMatch.closest_available_price);

  // Budget is only the blocker when the backend says so AND the suggestion is
  // genuinely higher than the current budget. The invariant is enforced here as
  // well as server-side, so a nonsensical "raise your budget to $7.86" against a
  // $37 budget can never render even if the payload is wrong.
  const budgetBlocked =
    noMatch.failed_constraints?.includes('max_price') &&
    typeof noMatch.closest_available_price === 'number' &&
    typeof noMatch.requested_max_price === 'number' &&
    noMatch.closest_available_price > noMatch.requested_max_price;
  const sizeBlocked = noMatch.failed_constraints?.includes('size');

  return (
    <section className="rounded-xl border border-amber-200 bg-amber-50/60 p-5">
      <div className="flex items-start gap-3">
        <SearchX className="mt-0.5 h-5 w-5 shrink-0 text-amber-700" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[17px] font-semibold tracking-tight text-slate-900">
            No exact matches
          </h2>
          <p className="mt-1 text-[14px] text-slate-700">
            {budgetBlocked
              ? 'We carry this, but not within your current budget.'
              : sizeBlocked
                ? `We found the right product type, but none of the retrieved products verify the ${noMatch.requested_size || 'requested'} size.`
              : noMatch.failed_constraints?.includes('audience')
                ? "Everything matching your other criteria is marketed to children, which doesn't fit who this is for."
                : "We couldn't find a close match for this product type in Pickly's catalog."}
          </p>

          {budgetBlocked && (
            <dl className="mt-3 grid gap-x-8 gap-y-2 sm:grid-cols-2 text-[13.5px]">
              {requested && (
                <div className="flex items-baseline gap-2">
                  <dt className="text-slate-500">Your budget:</dt>
                  <dd className="font-medium text-slate-900 tabular-nums">≤ {requested}</dd>
                </div>
              )}
              <div className="flex items-baseline gap-2">
                <dt className="text-slate-500">Closest matching options start at:</dt>
                <dd className="font-medium text-slate-900 tabular-nums">{closest}</dd>
              </div>
            </dl>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            {budgetBlocked && onRaiseBudget && (
              <button
                type="button"
                onClick={() => onRaiseBudget(noMatch.closest_available_price)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-[13px] font-medium text-slate-800 hover:bg-slate-50 transition-colors"
              >
                <ArrowUpRight className="h-3.5 w-3.5" />
                Raise budget to {closest}
              </button>
            )}
            {onSearchOnline && (
              <button
                type="button"
                onClick={onSearchOnline}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-[13px] font-medium text-slate-800 hover:bg-slate-50 transition-colors"
              >
                <Globe className="h-3.5 w-3.5" />
                Search online
              </button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
