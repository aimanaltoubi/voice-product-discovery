import React, { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

const STEP_LABELS = {
  router: 'Router — Intent & Constraints',
  safety: 'Safety Gate — Blocked Request',
  planner: 'Planner — Sources & Filters',
  'rag.search': 'rag.search — Private Retrieval (MCP)',
  'web.search': 'web.search — Live Comparison (MCP)',
  reconcile: 'Reconcile — Conflict Handling',
  answerer: 'Answerer/Critic — Synthesis'
};

export default function AgentStepLog({ steps }) {
  const [open, setOpen] = useState({});
  if (!steps?.length) return null;

  const toggle = (i) => setOpen((o) => ({ ...o, [i]: !o[i] }));

  return (
    <div className="space-y-2">
      {steps.map((s, i) => (
        <div key={i} className="rounded-xl border border-border bg-card overflow-hidden">
          <button
            type="button"
            onClick={() => toggle(i)}
            className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-accent/40 transition-colors"
          >
            <span className="flex items-center gap-2">
              {open[i] ? (
                <ChevronDown className="w-4 h-4 text-muted-foreground" />
              ) : (
                <ChevronRight className="w-4 h-4 text-muted-foreground" />
              )}
              <span className="font-medium text-sm">{STEP_LABELS[s.name] || s.name}</span>
            </span>
            <span className="text-[11px] text-muted-foreground font-mono">{s.timestamp}</span>
          </button>
          {open[i] && (
            <div className="px-4 pb-4 space-y-3 text-xs">
              <div>
                <div className="text-muted-foreground uppercase tracking-wide mb-1">Input</div>
                <pre className="bg-muted/60 rounded-lg p-3 overflow-auto max-h-48 font-mono leading-relaxed">
                  {JSON.stringify(s.input, null, 2)}
                </pre>
              </div>
              <div>
                <div className="text-muted-foreground uppercase tracking-wide mb-1">Output</div>
                <pre className="bg-muted/60 rounded-lg p-3 overflow-auto max-h-64 font-mono leading-relaxed">
                  {typeof s.output === 'string' ? s.output : JSON.stringify(s.output, null, 2)}
                </pre>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
