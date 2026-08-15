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
                <span className="font-mono text-[12px] text-slate-500">{c.doc_id}</span>
                <span className="mx-1.5 text-slate-300">·</span>
                <span className="text-slate-700">{c.title}</span>
                {c.brand && c.brand.toLowerCase() !== 'unknown' && (
                  <span className="text-slate-500"> — {c.brand}</span>
                )}
              </span>
            </>
          ) : (
            <>
              <ExternalLink className="mt-0.5 h-3.5 w-3.5 shrink-0 text-teal-600" />
              <a
                href={c.url}
                target="_blank"
                rel="noreferrer"
                className="min-w-0 break-all text-teal-700 hover:underline"
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
