import React, { useState } from 'react';
import { X, ArrowUp, ArrowDown } from 'lucide-react';

/**
 * Compact structured search state, rendered separately below the conversation.
 *
 * Structured constraints (product type, size, budget, audience, use case) are
 * shown as plain chips. Soft preferences are removable: removing one reruns the
 * whole search with the remaining constraints. Product type is deliberately not
 * removable — dropping it is a new search, which is what the reset control is
 * for.
 */
const money = (v) =>
  Number.isInteger(v) ? `$${v.toLocaleString()}` : `$${v.toFixed(2)}`;

function Chip({ children, onRemove, label, onPromote, onDemote }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 py-1 pl-2 pr-1 text-[13px] leading-none text-slate-800">
      {children}
      {onPromote && (
        <button
          type="button"
          onClick={onPromote}
          aria-label={`Make ${label} a must-have`}
          title="Make must-have"
          className="ml-0.5 rounded p-0.5 text-slate-400 transition-colors hover:bg-slate-200 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
        >
          <ArrowUp className="h-3 w-3" />
        </button>
      )}
      {onDemote && (
        <button
          type="button"
          onClick={onDemote}
          aria-label={`Make ${label} a preference`}
          title="Make preference"
          className="ml-0.5 rounded p-0.5 text-slate-400 transition-colors hover:bg-slate-200 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
        >
          <ArrowDown className="h-3 w-3" />
        </button>
      )}
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

export default function YourSearch({ session, onRemoveFeature, onSetPriority, onRemoveColor }) {
  const [editing, setEditing] = useState(false);
  const c = session?.constraints;
  if (!c?.product_type) return null;

  const features = c.qualitative_features || [];
  const required = c.required_features || [];
  const isRequired = (f) => required.some((r) => r.toLowerCase() === f.toLowerCase());
  // Must-haves are the constraints already enforced as hard (product type,
  // budget, size, brand, material) plus any preference the user promoted.
  const colorRequired = c.color ? isRequired(c.color) : false;
  const prefs = features.filter((f) => !isRequired(f));
  const promoted = features.filter(isRequired);

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold text-slate-900">Current search</h2>

      <p className="mb-1.5 text-[11.5px] font-medium uppercase tracking-wide text-slate-400">
        Must-haves
      </p>
      <div className="flex flex-wrap gap-1.5">
        <Chip>{c.product_type}</Chip>
        {c.size && <Chip>{c.size}</Chip>}
        {typeof c.budget === 'number' && <Chip>≤ {money(c.budget)}</Chip>}
        {c.brand && <Chip>{c.brand}</Chip>}
        {c.material && <Chip>{c.material}</Chip>}
        {c.color && colorRequired && (
          <Chip
            label={c.color}
            onRemove={() => onRemoveColor && onRemoveColor()}
            onDemote={editing && onSetPriority ? () => onSetPriority(c.color, false) : null}
          >
            {c.color}
          </Chip>
        )}
        {promoted.map((f) => (
          <Chip
            key={f}
            label={f}
            onRemove={() => onRemoveFeature(f)}
            onDemote={editing && onSetPriority ? () => onSetPriority(f, false) : null}
          >
            {f}
          </Chip>
        ))}
      </div>

      {(prefs.length > 0 || c.eco_friendly) && (
        <>
          <p className="mb-1.5 mt-3 text-[11.5px] font-medium uppercase tracking-wide text-slate-400">
            Preferences
          </p>
          <div className="flex flex-wrap gap-1.5">
            {c.eco_friendly && <Chip>Eco-friendly</Chip>}
            {prefs.map((f) => (
              <Chip
                key={f}
                label={f}
                onRemove={() => onRemoveFeature(f)}
                onPromote={editing && onSetPriority ? () => onSetPriority(f, true) : null}
              >
                {f}
              </Chip>
            ))}
          </div>
        </>
      )}

      {c.color && !colorRequired && (
        <>
          <p className="mb-1.5 mt-3 text-[11.5px] font-medium uppercase tracking-wide text-slate-400">
            Color
          </p>
          <div className="flex flex-wrap gap-1.5">
            <Chip
              label={c.color}
              onRemove={() => onRemoveColor && onRemoveColor()}
              onPromote={editing && onSetPriority ? () => onSetPriority(c.color, true) : null}
            >
              {c.color}
            </Chip>
          </div>
        </>
      )}

      {c.audience && (
        <>
          <p className="mb-1.5 mt-3 text-[11.5px] font-medium uppercase tracking-wide text-slate-400">
            Audience
          </p>
          <div className="flex flex-wrap gap-1.5"><Chip>{c.audience}</Chip></div>
        </>
      )}

      {c.use_case && (
        <>
          <p className="mb-1.5 mt-3 text-[11.5px] font-medium uppercase tracking-wide text-slate-400">
            Use case
          </p>
          <div className="flex flex-wrap gap-1.5"><Chip>{c.use_case}</Chip></div>
        </>
      )}

      {(features.length > 0 || c.color) && onSetPriority && (
        <button
          type="button"
          onClick={() => setEditing((v) => !v)}
          aria-pressed={editing}
          className="mt-3 rounded text-[12.5px] font-medium text-slate-500 underline underline-offset-2 transition-colors hover:text-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
        >
          {editing ? 'Done editing priorities' : 'Edit priorities'}
        </button>
      )}
      {editing && (
        <p className="mt-1.5 text-[12px] text-slate-500">
          Use ↑ to make a preference required, or ↓ to make it optional. Required
          preferences must be stated in the product listing to count as a match.
        </p>
      )}
    </section>
  );
}
