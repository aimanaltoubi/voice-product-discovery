import React, { useEffect, useState } from 'react';
import { transcribe, discover, speak, health } from '@/api/client';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Volume2, Loader2, AlertCircle, Search, MessageCircleQuestion, ExternalLink } from 'lucide-react';
import MicRecorder from '@/components/discovery/MicRecorder';
import AgentTrace from '@/components/discovery/AgentTrace';
import ConstraintPanel from '@/components/discovery/ConstraintPanel';
import ProductResults from '@/components/discovery/ProductResults';
import NoMatchState from '@/components/discovery/NoMatchState';
import CatalogBadge from '@/components/discovery/CatalogBadge';
import CitationList from '@/components/discovery/CitationList';
import RefineActions from '@/components/discovery/RefineActions';
import YourSearch from '@/components/discovery/YourSearch';
import ComparisonTable from '@/components/discovery/ComparisonTable';
import BrandHeader from '@/components/discovery/BrandHeader';

export default function Home() {
  const [transcript, setTranscript] = useState('');
  const [busy, setBusy] = useState('');
  const [result, setResult] = useState(null);
  const [ttsUrl, setTtsUrl] = useState(null);
  const [error, setError] = useState('');
  // The active search session: accumulated constraints + refinement history.
  // Browser-held and passed explicitly on every request — no server session.
  const [session, setSession] = useState(null);
  const [lastWasVoice, setLastWasVoice] = useState(false);
  // Fetched once here and shared with CatalogBadge so the capability line can
  // state a real product count instead of a hardcoded one.
  const [catalog, setCatalog] = useState(null);

  useEffect(() => {
    let alive = true;
    health().then((h) => alive && setCatalog(h.catalog)).catch(() => {});
    return () => { alive = false; };
  }, []);

  const handleAudio = async (blob) => {
    setError('');
    setBusy('transcribing');
    try {
      const res = await transcribe(blob);
      const t = res.transcript || '';
      setTranscript(t);
      setLastWasVoice(true);
      // Voice follows the same path as text: if a session is active this
      // utterance is a refinement of it, not a new search.
      if (t.trim()) await runDiscovery(t);
      else setBusy('');
    } catch (e) {
      setError(e.message);
      setBusy('');
    }
  };

  const synthesize = async (text) => {
    try {
      setBusy('speaking');
      const res = await speak(text);
      setTtsUrl(res.audio_url);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy('');
    }
  };

  /**
   * @param text     the utterance (typed, spoken, or from a quick action)
   * @param override explicit session context; `null` forces a brand-new search
   */
  const runDiscovery = async (text, override = undefined, mode = null) => {
    const q = (typeof text === 'string' ? text : transcript).trim();
    if (!q) return;
    const ctx = override !== undefined
      ? override
      : (session?.constraints?.product_type
        ? { constraints: session.constraints, top_k: session.top_k }
        : null);

    setError('');
    setBusy('running');
    setTtsUrl(null);
    setResult(null);
    try {
      const res = await discover(q, ctx, mode);
      setResult(res);
      // Roll the returned merged context forward as the new session.
      setSession((prev) => {
        const merged = res.search_context?.constraints || {};
        if (!merged.product_type) return prev;
        const isRefinement = Boolean(ctx);
        return {
          constraints: merged,
          top_k: res.search_context?.top_k,
          originalQuery: isRefinement ? prev?.originalQuery || q : q,
          history: isRefinement
            ? [...(prev?.history || []), { utterance: q, changes: res.constraint_changes || [] }]
            : [],
        };
      });
      if (res.spoken_answer) await synthesize(res.spoken_answer);
      else setBusy('');
    } catch (e) {
      setError(e.message);
      setBusy('');
    }
  };

  /** Remove one soft preference and rerun the full search without it. */
  const removeFeature = (feature) => {
    if (!session?.constraints) return;
    const kept = (session.constraints.qualitative_features || []).filter(
      (f) => f.toLowerCase() !== feature.toLowerCase()
    );
    const constraints = { ...session.constraints, qualitative_features: kept };
    setSession((s) => ({ ...s, constraints }));
    // mode "apply": the constraints are already correct, so extraction is
    // skipped — otherwise the word "soft" in this sentence would be parsed
    // straight back into the search we just removed it from.
    runDiscovery(
      `Remove the "${feature}" preference.`,
      { constraints, top_k: session.top_k, changes: [`Removed: ${feature}`] },
      'apply'
    );
  };

  const resetSession = () => {
    setSession(null);
    setResult(null);
    setTranscript('');
    setTtsUrl(null);
    setError('');
    setBusy('');
  };

  const playTts = () => {
    if (result?.spoken_answer) synthesize(result.spoken_answer);
  };

  // No-match recovery: both stay inside the active session, so the user never
  // has to restate the product. The Planner still chooses the sources.
  const raiseBudget = (to) => {
    refine(`Actually, raise my budget to $${Math.ceil(to)}.`);
  };
  const searchOnline = () => {
    refine('What is the current online price and availability of these right now?');
  };

  const refine = (query) => {
    setTranscript(query);
    runDiscovery(query);
  };

  const hasSession = Boolean(session?.constraints?.product_type);
  const isNoMatch = Boolean(result?.no_match);
  const isClarify = Boolean(result?.clarify?.question);
  const rows = result?.comparison_table || [];
  const altRows = result?.no_match?.closest_alternatives || [];

  return (
    <div className="min-h-screen bg-slate-50/60 text-slate-900 antialiased">
      <main className="mx-auto max-w-3xl space-y-6 px-5 py-8 sm:px-6 sm:py-10">
        {/* Level 1 — brand, then the primary action */}
        <BrandHeader compact={hasSession} />

        <YourSearch
          session={session}
          onRemoveFeature={removeFeature}
          onReset={resetSession}
        />

        {/* Search / refine input */}
        <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
          <label htmlFor="query" className="sr-only">
            {hasSession ? 'Refine your search' : 'Describe what you are looking for'}
          </label>
          <Textarea
            id="query"
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            placeholder={
              hasSession
                ? "Tell me what you'd change — e.g. something lighter, or under $40"
                : "Soft twin comforter under $50 that's easy to wash…"
            }
            className="min-h-[68px] resize-none border-slate-200 text-[15.5px] focus-visible:ring-slate-400"
          />
          <div className="mt-3 flex flex-wrap items-center gap-2.5">
            <Button
              onClick={() => runDiscovery()}
              disabled={!transcript.trim() || !!busy}
              className="h-10 gap-2 rounded-lg bg-slate-900 px-5 text-[14px] font-medium text-white hover:bg-slate-800"
            >
              {busy === 'running'
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Search className="h-4 w-4" />}
              {busy === 'running'
                ? 'Searching…'
                : hasSession ? 'Update search' : 'Find products'}
            </Button>
            <MicRecorder onAudio={handleAudio} disabled={!!busy} />
            {busy === 'transcribing' && (
              <span className="flex items-center gap-1.5 text-[13px] text-slate-500">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Transcribing…
              </span>
            )}
          </div>

          {!hasSession && !busy && (
            <div className="mt-4 border-t border-slate-100 pt-3">
              <p className="mb-2 text-[12.5px] text-slate-500">Try asking</p>
              <div className="flex flex-wrap gap-2">
                {/* Each verified to return real private-catalog results — a
                    landing example that falls through to web search would make
                    the catalog look empty. */}
                {[
                  "Soft twin comforter under $50 that's easy to wash",
                  'A challenging 1000 piece jigsaw puzzle under $30',
                  'Plush stuffed animal for a toddler under $20',
                ].map((ex) => (
                  <button
                    key={ex}
                    type="button"
                    disabled={!!busy}
                    onClick={() => { setLastWasVoice(false); setTranscript(ex); runDiscovery(ex); }}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-left text-[13px] text-slate-600 transition-colors hover:border-slate-300 hover:bg-slate-100 hover:text-slate-900 active:bg-slate-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}

          {error && (
            <div className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 p-3 text-[13px] text-red-800">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span className="break-words">{error}</span>
            </div>
          )}

          {/* Quiet capability line. The count comes from /api/health, so it is
              never hardcoded and simply omits itself if health is unavailable
              or the catalog is in sample mode (CatalogBadge carries the
              sample-data warning). */}
          <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            {!hasSession && (
              <p className="text-[12px] text-slate-400">
                {catalog?.is_real_data && typeof catalog.count === 'number'
                  ? `${catalog.count.toLocaleString()} products · `
                  : ''}
                Grounded recommendations · Live checks when needed
              </p>
            )}
            <div className="ml-auto">
              <CatalogBadge catalog={catalog} />
            </div>
          </div>
        </section>

        {busy === 'running' && (
          <p className="flex items-center justify-center gap-2 py-6 text-[14px] text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            Finding the best matches…
          </p>
        )}

        {result && (
          <>
            {/* Transcript */}
            {result.transcript && (
              <section className="rounded-xl border border-slate-200 bg-white p-5">
                <h2 className="mb-1.5 text-sm font-semibold text-slate-900">
                  You said
                  {lastWasVoice && (
                    <span className="ml-2 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[11px] font-medium text-slate-500">
                      via voice · Whisper
                    </span>
                  )}
                </h2>
                <p className="text-[14.5px] leading-relaxed text-slate-700">“{result.transcript}”</p>
              </section>
            )}

            {/* Router transparency. On a refinement this is only the delta —
                the accumulated state already lives in "Your search" above — so
                it stays a quiet one-liner rather than a repeated chip wall. */}
            {result.constraint_changes?.length > 0 ? (
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 px-1 text-[13px] text-slate-500">
                <span className="font-medium text-slate-600">Latest update</span>
                {result.constraint_changes.map((c) => (
                  <span
                    key={c}
                    className="rounded border border-slate-200 bg-white px-1.5 py-0.5 text-slate-700"
                  >
                    {c}
                  </span>
                ))}
              </p>
            ) : (
              <ConstraintPanel
                understood={result.understood}
                topK={result.top_k}
                needsLive={result.needs_live}
              />
            )}

            {/* One clarifying question for a request too broad to rank on. */}
            {isClarify && (
              <section className="rounded-xl border border-slate-300 bg-white p-5">
                <div className="flex items-start gap-3">
                  <MessageCircleQuestion className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" />
                  <div className="min-w-0 flex-1">
                    <h2 className="text-[17px] font-semibold tracking-tight text-slate-900">
                      A little more detail will help
                    </h2>
                    <p className="mt-1 text-[14.5px] leading-relaxed text-slate-700">
                      {result.clarify.question}
                    </p>
                    <p className="mt-2 text-[12.5px] text-slate-500">
                      Pick one to continue, or add a detail above:
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {['for everyday use', 'for the gym', 'for travel', 'for a child'].map((s) => (
                        <button
                          key={s}
                          type="button"
                          onClick={() =>
                            // Refinement of the session the clarify node opened,
                            // so the product type carries over automatically.
                            refine(`It's ${s}.`)
                          }
                          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-[13px] font-medium text-slate-700 transition-colors hover:bg-slate-50 hover:text-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
                        >
                          {s.replace(/^for /, '')}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </section>
            )}

            {isNoMatch && (
              <NoMatchState
                noMatch={result.no_match}
                onRaiseBudget={raiseBudget}
                onSearchOnline={searchOnline}
              />
            )}

            {/* Level 2 — the products are why the user is here */}
            {!isNoMatch && rows.length > 0 && (
              <>
                <ProductResults
                  rows={rows}
                  requested={result.understood?.qualitative_features || []}
                  reconciliation={result.reconciliation}
                />
                <ComparisonTable rows={rows} reconciliation={result.reconciliation} />
              </>
            )}

            {isNoMatch && altRows.length > 0 && (
              <ProductResults rows={altRows} variant="alternatives" />
            )}

            {result.search_links?.length > 0 && (
              <section className="rounded-xl border border-slate-200 bg-white p-5">
                <h2 className="text-[15px] font-semibold text-slate-900">Check ratings directly</h2>
                <p className="mt-1 text-[13px] text-slate-500">
                  These are search pages, not verified product recommendations.
                </p>
                <ul className="mt-3 space-y-2">
                  {result.search_links.map((link) => (
                    <li key={link.url}>
                      <a
                        href={link.url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1.5 rounded text-[13.5px] font-medium text-teal-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
                      >
                        <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                        {link.title || 'Open search page'}
                      </a>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* Level 3 — narrow it down */}
            {!isClarify && rows.length > 0 && (
              <section className="rounded-xl border border-slate-200 bg-white p-5">
                <h2 className="text-[15px] font-semibold text-slate-900">Narrow it down</h2>
                <p className="mt-0.5 mb-3 text-[13px] text-slate-500">
                  Adjust your search — everything above updates.
                </p>
                <RefineActions
                  understood={result.understood}
                  onRefine={refine}
                  disabled={!!busy}
                />
                <p className="mt-3 text-[12.5px] text-slate-500">
                  Or{' '}
                  <button
                    type="button"
                    onClick={() => {
                      const el = document.getElementById('query');
                      el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                      el?.focus();
                    }}
                    className="rounded font-medium text-slate-700 underline underline-offset-2 hover:text-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
                  >
                    tell me what you'd change
                  </button>{' '}
                  in your own words, by text or voice.
                </p>
              </section>
            )}

            {/* Level 4 — recommendation + audio */}
            {result.spoken_answer && (
              <section className="rounded-xl border border-slate-200 bg-white p-5">
                <h2 className="mb-2 text-sm font-semibold text-slate-900">Recommendation</h2>
                {/* Detailed text on screen; TTS speaks the short summary. */}
                <p className="text-[15px] leading-relaxed text-slate-800">
                  {result.answer_detail || result.spoken_answer}
                </p>
                <div className="mt-4 flex flex-wrap items-center gap-3">
                  <Button
                    onClick={playTts}
                    disabled={busy === 'speaking'}
                    className="h-10 gap-2 rounded-lg bg-slate-900 px-4 text-[14px] font-medium text-white hover:bg-slate-800"
                  >
                    {busy === 'speaking'
                      ? <Loader2 className="h-4 w-4 animate-spin" />
                      : <Volume2 className="h-4 w-4" />}
                    {busy === 'speaking'
                      ? 'Synthesizing…'
                      : ttsUrl ? 'Replay recommendation' : 'Play recommendation'}
                  </Button>
                  <span className="text-[12.5px] text-slate-500">
                    Spoken summary ≈{result.spoken_answer.split(/\s+/).length} words
                  </span>
                </div>
                {ttsUrl && <audio controls autoPlay src={ttsUrl} className="mt-3 w-full" />}
              </section>
            )}

            {/* Level 5 — provenance */}
            {result.citations?.length > 0 && (
              <section className="rounded-xl border border-slate-200 bg-white p-5">
                <h2 className="mb-3 text-sm font-semibold text-slate-900">Sources</h2>
                <CitationList citations={result.citations} />
              </section>
            )}

            {/* Level 6 — technical transparency, last and collapsed */}
            <AgentTrace steps={result.steps} />
          </>
        )}
      </main>
    </div>
  );
}
