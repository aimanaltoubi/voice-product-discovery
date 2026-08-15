import React, { useEffect, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Database, Globe } from 'lucide-react';

/**
 * Agent Trace — the real graph execution, revealed with a staggered fade.
 *
 * Every label/detail comes from the backend step objects (graph/nodes.py:step).
 * The stagger is presentation only: the graph has already finished by the time
 * this renders, so this is NOT streaming and is not labelled as such.
 */
const STAGGER_MS = 140;

const SOURCE = {
  'rag.search': { icon: Database, tone: 'text-slate-700 bg-slate-100 border-slate-200', tag: 'Private' },
  'web.search': { icon: Globe, tone: 'text-teal-800 bg-teal-50 border-teal-200', tag: 'Live web' },
};

export default function AgentTrace({ steps }) {
  // Collapsed by default: the trace proves the architecture, but the products
  // are what the page is for.
  const [expanded, setExpanded] = useState(false);
  const [shown, setShown] = useState(0);
  const [openRaw, setOpenRaw] = useState(false);

  // The stagger runs when the panel opens, not on load, so the reveal is
  // actually seen. The graph has already finished — this is not streaming.
  useEffect(() => {
    if (!expanded || !steps?.length) return;
    setShown(0);
    const timers = steps.map((_, i) =>
      setTimeout(() => setShown((n) => Math.max(n, i + 1)), i * STAGGER_MS)
    );
    return () => timers.forEach(clearTimeout);
  }, [expanded, steps]);

  if (!steps?.length) return null;

  const usedWeb = steps.some((s) => (s.stage || s.name) === 'web.search');
  const summary = `${steps.length} step${steps.length === 1 ? '' : 's'} · ${
    usedWeb ? 'Private + live web' : 'Private catalog'
  }`;

  return (
    <section className="rounded-xl border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between gap-3 rounded-xl px-5 py-4 text-left transition-colors hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
      >
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-slate-900">
            How the assistant worked
          </span>
          <span className="block text-[12.5px] text-slate-500">{summary}</span>
        </span>
        <ChevronRight
          className={`h-4 w-4 shrink-0 text-slate-400 transition-transform duration-200 ${
            expanded ? 'rotate-90' : ''
          }`}
          aria-hidden="true"
        />
      </button>

      {!expanded ? null : (
      <div className="px-5 pb-5">
      <ol className="space-y-2.5">
        {steps.map((s, i) => {
          const src = SOURCE[s.stage || s.name];
          const Icon = src?.icon;
          return (
            <li
              key={i}
              className={[
                'flex items-start gap-3 transition-all duration-300 ease-out motion-reduce:transition-none',
                i < shown ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-1',
              ].join(' ')}
            >
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-50 border border-emerald-200">
                <Check className="h-3 w-3 text-emerald-700" strokeWidth={3} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-slate-900">
                    {s.label || s.name}
                  </span>
                  {src && (
                    <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium ${src.tone}`}>
                      {Icon && <Icon className="h-3 w-3" />}
                      {src.tag}
                    </span>
                  )}
                </div>
                {s.detail && (
                  <p className="text-[13px] text-slate-500 mt-0.5 break-words">{s.detail}</p>
                )}
              </div>
              <span className="hidden sm:block text-[11px] font-mono text-slate-400 shrink-0">
                {s.timestamp}
              </span>
            </li>
          );
        })}
      </ol>

      <button
        type="button"
        onClick={() => setOpenRaw((v) => !v)}
        aria-expanded={openRaw}
        className="mt-4 flex items-center gap-1.5 rounded text-[13px] text-slate-500 transition-colors hover:text-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
      >
        {openRaw ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        Raw step data
      </button>

      {openRaw && (
        <div className="mt-3 space-y-3">
          {steps.map((s, i) => (
            <div key={i} className="rounded-lg border border-slate-200 overflow-hidden">
              <div className="bg-slate-50 px-3 py-1.5 text-[12px] font-mono text-slate-600 border-b border-slate-200">
                {s.stage || s.name}
              </div>
              <pre className="p-3 text-[11px] leading-relaxed font-mono overflow-x-auto max-h-56 text-slate-700">
                {JSON.stringify({ input: s.input, output: s.output }, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      )}
      </div>
      )}
    </section>
  );
}
