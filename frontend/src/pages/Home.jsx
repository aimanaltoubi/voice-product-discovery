import React, { useState } from 'react';
import { transcribe, discover, speak } from '@/api/client';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Volume2, Loader2, AlertCircle, Search, MessageCircleQuestion } from 'lucide-react';
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
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-3 px-5 py-4 sm:px-6">
          <div className="min-w-0">
            <h1 className="text-[17px] font-semibold tracking-tight">Product Discovery</h1>
            <p className="text-[13px] text-slate-500">Find products using voice or text</p>
          </div>
          <CatalogBadge />
        </div>
      </header>

      <main className="mx-auto max-w-3xl space-y-5 px-5 py-6 sm:px-6 sm:py-8">
        <YourSearch
          session={session}
          onRemoveFeature={removeFeature}
          onReset={resetSession}
        />

        {/* Input */}
        <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
          <label
            htmlFor="query"
            className="mb-2 block text-sm font-semibold text-slate-900"
          >
            {hasSession ? 'Refine your search' : 'What are you looking for?'}
          </label>
          <Textarea
            id="query"
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            placeholder={
              hasSession
                ? 'e.g. Make it machine washable · Something lighter · Actually under $40'
                : 'e.g. Recommend a soft, easy-to-wash comforter set for a twin bed under fifty dollars'
            }
            className="min-h-[76px] resize-none border-slate-200 text-[15px] focus-visible:ring-slate-400"
          />
          <div className="mt-3 flex flex-wrap items-center gap-2.5">
            <Button
              onClick={() => runDiscovery()}
              disabled={!transcript.trim() || !!busy}
              className="h-9 gap-2 rounded-lg bg-slate-900 px-4 text-[13.5px] font-medium text-white hover:bg-slate-800"
            >
              {busy === 'running'
                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                : <Search className="h-3.5 w-3.5" />}
              {busy === 'running' ? 'Running…' : hasSession ? 'Refine' : 'Search'}
            </Button>
            <MicRecorder onAudio={handleAudio} disabled={!!busy} />
            {busy === 'transcribing' && (
              <span className="flex items-center gap-1.5 text-[13px] text-slate-500">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Transcribing…
              </span>
            )}
          </div>

          {error && (
            <div className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 p-3 text-[13px] text-red-800">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span className="break-words">{error}</span>
            </div>
          )}
        </section>

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

            {/* "What I understood" = this utterance. The accumulated state
                lives in "Your search" above, so a refinement shows only the
                delta rather than repeating every chip. */}
            {result.constraint_changes?.length > 0 ? (
              <section className="rounded-xl border border-slate-200 bg-white p-5">
                <h2 className="mb-2 text-sm font-semibold text-slate-900">
                  What I understood
                </h2>
                <ul className="flex flex-wrap gap-1.5">
                  {result.constraint_changes.map((c) => (
                    <li
                      key={c}
                      className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[13px] leading-none text-slate-800"
                    >
                      {c}
                    </li>
                  ))}
                </ul>
              </section>
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
                    <h2 className="text-sm font-semibold text-slate-900">
                      One quick question
                    </h2>
                    <p className="mt-1 text-[14.5px] leading-relaxed text-slate-700">
                      {result.clarify.question}
                    </p>
                    <p className="mt-2 text-[12.5px] text-slate-500">
                      Add a detail above and search again — or pick a starting point:
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

            {!isNoMatch && rows.length > 0 && (
              <>
                <ProductResults rows={rows} />
                <ComparisonTable rows={rows} reconciliation={result.reconciliation} />
              </>
            )}

            {isNoMatch && altRows.length > 0 && (
              <ProductResults rows={altRows} variant="alternatives" />
            )}

            {!isClarify && rows.length > 0 && (
              <RefineActions understood={result.understood} onRefine={refine} />
            )}

            <AgentTrace steps={result.steps} />

            {/* Recommendation + audio */}
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

            {result.citations?.length > 0 && (
              <section className="rounded-xl border border-slate-200 bg-white p-5">
                <h2 className="mb-3 text-sm font-semibold text-slate-900">Sources</h2>
                <CitationList citations={result.citations} />
              </section>
            )}
          </>
        )}

        {!result && !busy && (
          <p className="py-10 text-center text-[13.5px] text-slate-500">
            Record a voice request or type one above to get started.
          </p>
        )}
      </main>
    </div>
  );
}
