import React from 'react';

/**
 * "What I understood" — a compact read-out of the Router's structured output.
 * Every value comes from result.understood / result.top_k; fields the router
 * left empty are omitted rather than rendered as "Unknown" or "None".
 */
const money = (v) =>
  Number.isInteger(v) ? `$${v.toLocaleString()}` : `$${v.toFixed(2)}`;

function Field({ label, children }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500 mb-1.5">
        {label}
      </div>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

function Chip({ children }) {
  return (
    <span className="inline-flex items-center rounded-md bg-slate-100 text-slate-800 border border-slate-200 px-2 py-1 text-[13px] leading-none">
      {children}
    </span>
  );
}

export default function ConstraintPanel({ understood, topK, needsLive }) {
  if (!understood) return null;
  const {
    product_type: productType,
    audience,
    use_case: useCase,
    budget,
    size,
    qualitative_features: features = [],
    brand,
    material,
    eco_friendly: eco,
  } = understood;

  const fields = [];
  if (productType) fields.push(<Field key="p" label="Product"><Chip>{productType}</Chip></Field>);
  if (audience) fields.push(<Field key="a" label="For"><Chip>{audience}</Chip></Field>);
  if (useCase) fields.push(<Field key="u" label="Use"><Chip>{useCase}</Chip></Field>);
  if (typeof budget === 'number') fields.push(<Field key="b" label="Budget"><Chip>≤ {money(budget)}</Chip></Field>);
  if (size) fields.push(<Field key="s" label="Size"><Chip>{size}</Chip></Field>);
  if (brand) fields.push(<Field key="br" label="Brand"><Chip>{brand}</Chip></Field>);
  if (material) fields.push(<Field key="m" label="Material"><Chip>{material}</Chip></Field>);
  if (eco) fields.push(<Field key="e" label="Preference"><Chip>Eco-friendly</Chip></Field>);
  if (features.length > 0) {
    fields.push(
      <Field key="q" label="Preferences">
        {features.map((f) => <Chip key={f}>{f}</Chip>)}
      </Field>
    );
  }
  if (topK) fields.push(<Field key="k" label="Results"><Chip>{topK}</Chip></Field>);
  if (needsLive) fields.push(<Field key="l" label="Information"><Chip>Current / live</Chip></Field>);

  if (fields.length === 0) return null;

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5">
      <h2 className="text-sm font-semibold text-slate-900 mb-4">What I understood</h2>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{fields}</div>
    </section>
  );
}
