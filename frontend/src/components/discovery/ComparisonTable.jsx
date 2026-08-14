import React from 'react';
import { Star } from 'lucide-react';

export default function ComparisonTable({ rows, topPickId }) {
  if (!rows?.length) return null;
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-muted/50 text-muted-foreground text-left">
            <th className="px-4 py-3 font-medium">Product</th>
            <th className="px-4 py-3 font-medium">Brand</th>
            <th className="px-4 py-3 font-medium">Price</th>
            <th className="px-4 py-3 font-medium">Rating</th>
            <th className="px-4 py-3 font-medium">Ingredients / Features</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const isTop = r.doc_id === topPickId;
            return (
              <tr key={r.doc_id} className={isTop ? 'bg-emerald-50/60' : ''}>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    {isTop && <span className="text-[10px] uppercase font-semibold text-emerald-700 bg-emerald-100 rounded-full px-2 py-0.5">Top pick</span>}
                    <span className="font-medium">{r.title}</span>
                  </div>
                  <div className="text-[11px] text-muted-foreground font-mono mt-0.5">{r.doc_id}</div>
                </td>
                <td className="px-4 py-3 text-muted-foreground">{r.brand}</td>
                <td className="px-4 py-3 font-mono">
                  {typeof r.price === 'number' ? `$${r.price.toFixed(2)}` : '—'}
                  {typeof r.price_per_oz === 'number' && (
                    <div className="text-[11px] text-muted-foreground">${r.price_per_oz.toFixed(2)}/oz</div>
                  )}
                </td>
                <td className="px-4 py-3">
                  {typeof r.rating === 'number' ? (
                    <span className="inline-flex items-center gap-1">
                      <Star className="w-3.5 h-3.5 fill-amber-400 text-amber-400" />
                      {r.rating.toFixed(1)}
                    </span>
                  ) : '—'}
                </td>
                <td className="px-4 py-3 text-muted-foreground max-w-xs">{r.ingredients || r.features}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
