import React from 'react';
import { FileText, ExternalLink } from 'lucide-react';

export default function CitationList({ citations }) {
  if (!citations?.length) return null;
  return (
    <div className="space-y-2">
      {citations.map((c, i) => (
        <div key={i} className="flex items-start gap-2 text-sm">
          {c.type === 'private' ? (
            <>
              <FileText className="w-4 h-4 text-emerald-600 mt-0.5 shrink-0" />
              <span>
                <span className="font-mono text-xs text-muted-foreground">{c.doc_id}</span>
                <span className="mx-2 text-muted-foreground">·</span>
                <span>{c.title}</span>
                <span className="text-muted-foreground"> — {c.brand}</span>
              </span>
            </>
          ) : (
            <a
              href={c.url}
              target="_blank"
              rel="noreferrer"
              className="flex items-start gap-2 text-blue-600 hover:underline"
            >
              <ExternalLink className="w-4 h-4 mt-0.5 shrink-0" />
              <span className="truncate">{c.url}</span>
            </a>
          )}
        </div>
      ))}
    </div>
  );
}