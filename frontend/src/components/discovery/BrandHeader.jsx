import React from 'react';
import { ListFilter } from 'lucide-react';

/**
 * Pickly brand header.
 *
 * The wordmark is the only element using the rounded display face
 * (`font-brand`); everything else in the app keeps the neutral sans stack.
 *
 * `compact` trims the mark slightly once a session exists, but the short
 * product promise remains so the left assistant panel always has context.
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

      <p className={`${compact ? 'mt-1 text-[12.5px]' : 'mt-2 text-[14px]'} text-slate-500`}>
        Find what fits, faster.
      </p>
    </header>
  );
}
