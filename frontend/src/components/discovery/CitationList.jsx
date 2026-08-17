import React from 'react';
import { FileText, ExternalLink } from 'lucide-react';

export default function CitationList({ citations }) {
  if (!citations?.length) return null;
  return (
    <ul className="space-y-2.5">
      {citations.map((c, i) => (
        <li key={i} className="flex items-start gap-2.5 text-[13.5px]">
          {c.type === 'private' ? (
            <>
              <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
              <span className="min-w-0">
                <span className="text-[12px] font-medium text-slate-500">Amazon 2020 dataset</span>
                <span className="mx-1.5 text-slate-300">·</span>
                <span className="font-mono text-[11px] text-slate-400">{c.doc_id}</span>
                <span className="block text-slate-700">{c.title}</span>
                {c.brand && c.brand.toLowerCase() !== 'unknown' && (
                  <span className="text-slate-500"> — {c.brand}</span>
                )}
                {c.product_url && (
                  <a
                    href={c.product_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-0.5 block w-fit rounded text-[12px] font-medium text-blue-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                  >
                    View on Amazon →
                  </a>
                )}
              </span>
            </>
          ) : (
            <>
              <ExternalLink className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-600" />
              <a
                href={c.url}
                target="_blank"
                rel="noopener noreferrer"
                className="min-w-0 break-all text-blue-700 hover:underline"
              >
                {c.title || c.url}
              </a>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}
