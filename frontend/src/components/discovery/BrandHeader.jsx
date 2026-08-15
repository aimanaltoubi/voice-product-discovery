import React from 'react';
import { ListFilter } from 'lucide-react';

/**
 * Pickly brand header.
 *
 * The wordmark is the only element using the rounded display face
 * (`font-brand`); everything else in the app keeps the neutral sans stack.
 *
 * `compact` is used once a search session exists: the tagline is marketing copy
 * for an empty page, and would only push the results down after that.
 */
export default function BrandHeader({ compact = false }) {
  return (
    <header className={compact ? '' : 'pt-2 sm:pt-4'}>
      <div className="flex items-center gap-2">
        {/* Mark: three lines narrowing to one — many options becoming a shortlist. */}
        <ListFilter
          aria-hidden="true"
          strokeWidth={2.5}
          className={`shrink-0 text-emerald-600 ${compact ? 'h-5 w-5' : 'h-6 w-6'}`}
        />
        <span
          className={`font-brand font-semibold leading-none tracking-tight text-slate-900 ${
            compact ? 'text-[24px]' : 'text-[28px] sm:text-[30px]'
          }`}
        >
          Pickly
        </span>
      </div>

      {!compact && (
        <>
          <h1 className="mt-5 text-[26px] font-semibold leading-tight tracking-tight text-slate-900 sm:text-[30px]">
            Find what fits, faster.
          </h1>
          <p className="mt-2 max-w-xl text-[15px] leading-relaxed text-slate-500">
            Describe what you need. Pickly finds the best matches and helps you
            narrow them down.
          </p>
        </>
      )}
    </header>
  );
}
