import React, { useEffect, useState } from 'react';
import { Database, AlertTriangle } from 'lucide-react';
import { health } from '@/api/client';

/**
 * Which catalog is actually serving rag.search, read from /api/health
 * (backed by backend/storage/catalog_meta.json). Sample data is never allowed
 * to look like the real Amazon catalog.
 */
export default function CatalogBadge() {
  const [catalog, setCatalog] = useState(null);

  useEffect(() => {
    let alive = true;
    health()
      .then((h) => alive && setCatalog(h.catalog))
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  if (!catalog || catalog.data_source === 'none') return null;

  const real = catalog.is_real_data;
  const count = typeof catalog.count === 'number' ? catalog.count.toLocaleString() : null;

  return (
    <span
      title={catalog.source_file || catalog.data_source}
      className={[
        'inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[12px] font-medium',
        real
          ? 'border-slate-200 bg-slate-50 text-slate-700'
          : 'border-amber-300 bg-amber-50 text-amber-900',
      ].join(' ')}
    >
      {real ? <Database className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
      {real ? 'Amazon 2020 catalog' : 'Sample catalog (not real data)'}
      {count && <span className="text-slate-400">·</span>}
      {count && <span className="tabular-nums text-slate-500">{count} products</span>}
    </span>
  );
}
