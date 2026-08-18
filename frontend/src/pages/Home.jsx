import React, { useEffect, useState } from 'react';
import { transcribe, discover, speak, health } from '@/api/client';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import {
  Volume2, Loader2, AlertCircle, Search, ExternalLink, ShoppingBag, RotateCcw,
} from 'lucide-react';
import MicRecorder from '@/components/discovery/MicRecorder';
import AgentTrace from '@/components/discovery/AgentTrace';
import ProductResults from '@/components/discovery/ProductResults';
import NoMatchState from '@/components/discovery/NoMatchState';
import NoLiveMatchState from '@/components/discovery/NoLiveMatchState';
import CatalogBadge from '@/components/discovery/CatalogBadge';
import CitationList from '@/components/discovery/CitationList';
import RefineActions from '@/components/discovery/RefineActions';
import ShoppingConversation from '@/components/discovery/ShoppingConversation';
import YourSearch from '@/components/discovery/YourSearch';
import ComparisonTable from '@/components/discovery/ComparisonTable';
import BrandHeader from '@/components/discovery/BrandHeader';

function AssistantOutcome({ result, busy, ttsUrl, onPlay, voiceTranscript }) {
  if (!result && !voiceTranscript) return null;

  const showOutcome = result && !result.clarify?.question;
  const hasSources = showOutcome && (
    result.citations?.length > 0 || result.search_links?.length > 0
  );

  return (
    <div className="space-y-5">
      {showOutcome && result.spoken_answer && (
        <section className="border-t border-slate-200 pt-5">
          <h2 className="text-sm font-semibold text-slate-900">Pickly's recommendation</h2>
          <blockquote className="mt-2.5 border-l-2 border-emerald-400 pl-3 text-[14px] leading-relaxed text-slate-700">
            {result.answer_detail || result.spoken_answer}
          </blockquote>
          <div className="mt-3 flex flex-wrap items-center gap-2.5">
            <Button
              onClick={onPlay}
              disabled={busy === 'speaking'}
              className="h-9 gap-2 rounded-lg bg-slate-900 px-3.5 text-[13px] font-medium text-white hover:bg-slate-800"
            >
              {busy === 'speaking'
                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                : <Volume2 className="h-3.5 w-3.5" />}
              {busy === 'speaking'
                ? 'Synthesizing…'
                : ttsUrl ? 'Replay recommendation' : 'Play recommendation'}
            </Button>
            <span className="text-[11.5px] text-slate-400">
              ≈{result.spoken_answer.split(/\s+/).length} spoken words
            </span>
          </div>
        </section>
      )}

      {voiceTranscript && (
        <section className="border-t border-slate-200 pt-5" aria-labelledby="voice-transcript-heading">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 id="voice-transcript-heading" className="text-sm font-semibold text-slate-900">
              Voice transcript
            </h2>
            <span className="text-[11px] text-slate-400">via voice · Whisper</span>
          </div>
          <p className="mt-2 text-[13px] leading-relaxed text-slate-600">
            “{voiceTranscript}”
          </p>
        </section>
      )}

      {hasSources && (
        <section className="border-t border-slate-200 pt-5">
          <h2 className="mb-3 text-sm font-semibold text-slate-900">Sources</h2>
          {result.citations?.length > 0 && (
            <CitationList citations={result.citations} />
          )}

          {result.search_links?.length > 0 && (
            <div className={result.citations?.length > 0 ? 'mt-4 border-t border-slate-100 pt-3' : ''}>
              <p className="text-[12px] text-slate-500">
                Reference searches are not verified recommendations.
              </p>
              <ul className="mt-2.5 space-y-2">
                {result.search_links.map((link) => (
                  <li key={link.url}>
                    <a
                      href={link.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 rounded text-[13px] font-medium text-blue-700 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                    >
                      <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                      {link.title || 'Open search page'}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {result && <AgentTrace steps={result.steps} />}
    </div>
  );
}

const money = (value) => (
  Number.isInteger(value) ? `$${value.toLocaleString()}` : `$${Number(value).toFixed(2)}`
);

function changeAcknowledgement(change) {
  let match = change.match(/^Color:\s*(.+?)\s*→\s*(.+)$/i);
  if (match) return `Color updated from ${match[1]} to ${match[2]}.`;
  match = change.match(/^Color:\s*(.+)$/i);
  if (match) return `Added ${match[1]} as your color preference.`;
  match = change.match(/^Budget:\s*(.+?)\s*→\s*(.+)$/i);
  if (match) return `Budget updated from ${match[1]} to ${match[2]}.`;
  match = change.match(/^Budget:\s*(?:≤\s*)?(.+)$/i);
  if (match) return `Budget set to ${match[1]}.`;
  match = change.match(/^Added:\s*(.+)$/i);
  if (match) return `Added ${match[1]}.`;
  match = change.match(/^Removed:\s*(.+)$/i);
  if (match) return `Removed ${match[1]}.`;
  match = change.match(/^Must-have:\s*(.+)$/i);
  if (match) return `${match[1]} is now a must-have.`;
  match = change.match(/^Preference:\s*(.+)$/i);
  if (match) return `${match[1]} is now optional.`;
  if (/live web search requested/i.test(change)) {
    return "I'm checking current online options while keeping your existing requirements.";
  }
  return `${change.replace(/[.]+$/, '')}.`;
}

/** Deterministic chat copy built only from the completed result and diffs. */
function acknowledgementFor(result) {
  if (result?.clarify?.question) return result.clarify.question;

  const changes = result?.constraint_changes || [];
  const changeText = changes.map(changeAcknowledgement).join(' ');
  const liveRequested = result?.needs_live || changes.some((c) => /live web/i.test(c));

  if (result?.no_match) {
    const message = "I couldn't find an exact match in Pickly's private index. You can relax a requirement or search online.";
    return changeText ? `${changeText} ${message}` : message;
  }
  if (result?.live_unverified) {
    const message = "I checked current online options, but couldn't verify a product that meets every requirement.";
    return changeText && !/checking current/i.test(changeText) ? `${changeText} ${message}` : message;
  }
  if (liveRequested) {
    return changeText || "I checked current online options while keeping your existing requirements.";
  }
  if (changeText) return changeText;

  const count = result?.comparison_table?.length || 0;
  const understood = result?.understood || {};
  const priorities = [
    understood.size,
    understood.product_type,
    typeof understood.budget === 'number' ? `under ${money(understood.budget)}` : null,
    ...(understood.qualitative_features || []).slice(0, 2),
  ].filter(Boolean).join(', ');
  if (count > 0) {
    return `I found ${count} option${count === 1 ? '' : 's'} that fit. I've prioritized ${priorities || 'your current requirements'}.`;
  }
  return "I've updated the search using your current requirements.";
}

export default function Home() {
  const [transcript, setTranscript] = useState('');
  const [busy, setBusy] = useState('');
  const [result, setResult] = useState(null);
  const [ttsUrl, setTtsUrl] = useState(null);
  const [error, setError] = useState('');
  // The active search session: accumulated constraints + refinement history.
  // Browser-held and passed explicitly on every request — no server session.
  const [session, setSession] = useState(null);
  // Presentation state only. The backend SearchContext above remains the
  // source of truth for every retrieval and refinement decision.
  const [conversation, setConversation] = useState([]);
  const [lastVoiceTranscript, setLastVoiceTranscript] = useState('');
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
      if (t.trim()) setLastVoiceTranscript(t.trim());
      // Voice follows the same path as text: if a session is active this
      // utterance is a refinement of it, not a new search.
      if (t.trim()) await runDiscovery(t, undefined, null, { viaVoice: true });
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
  const runDiscovery = async (
    text, override = undefined, mode = null, presentation = {},
  ) => {
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
    setTranscript('');
    setConversation((turns) => [
      ...turns,
      { role: 'user', text: q, viaVoice: Boolean(presentation.viaVoice) },
    ]);
    try {
      const res = await discover(q, ctx, mode);
      setResult(res);
      setConversation((turns) => [
        ...turns,
        { role: 'assistant', type: 'update', text: acknowledgementFor(res) },
      ]);
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
      setTranscript(q);
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

  /** Move a preference between must-have and preference, then rerun. */
  const setPriority = (feature, makeRequired) => {
    if (!session?.constraints) return;
    const current = session.constraints.required_features || [];
    const others = current.filter((f) => f.toLowerCase() !== feature.toLowerCase());
    const required_features = makeRequired ? [...others, feature] : others;
    const constraints = { ...session.constraints, required_features };
    setSession((s) => ({ ...s, constraints }));
    // "apply" so the sentence describing the change is never re-parsed back
    // into the search it is describing.
    runDiscovery(
      makeRequired ? `Require "${feature}".` : `Make "${feature}" optional.`,
      {
        constraints,
        top_k: session.top_k,
        changes: [makeRequired ? `Must-have: ${feature}` : `Preference: ${feature}`],
      },
      'apply'
    );
  };

  /** Clear the colour. The active context is the source of truth, so the old
   *  colour cannot leak back in from earlier refinement text. */
  const removeColor = () => {
    if (!session?.constraints) return;
    const gone = session.constraints.color;
    const constraints = {
      ...session.constraints,
      color: null,
      required_features: (session.constraints.required_features || []).filter(
        (f) => f.toLowerCase() !== String(gone || '').toLowerCase()
      ),
    };
    setSession((s) => ({ ...s, constraints }));
    runDiscovery(
      `Remove the colour preference.`,
      { constraints, top_k: session.top_k, changes: [`Removed: ${gone}`] },
      'apply'
    );
  };

  const resetSession = () => {
    setSession(null);
    setConversation([]);
    setLastVoiceTranscript('');
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
  // "Search online" is an explicit user action, so it must not depend on the
  // Router inferring live intent from prose. mode "apply" carries the current
  // constraints through verbatim and sets needs_live directly — the product
  // type, size, budget and preferences all survive.
  const searchOnline = () => {
    if (!session?.constraints) {
      refine('What is the current online price and availability right now?');
      return;
    }
    runDiscovery(
      'Check current online prices and availability.',
      {
        constraints: session.constraints,
        top_k: session.top_k,
        needs_live: true,
        changes: ['Live web search requested'],
      },
      'apply'
    );
  };

  const refine = (query) => {
    runDiscovery(query);
  };

  const hasSession = Boolean(session?.constraints?.product_type);
  const isNoMatch = Boolean(result?.no_match);
  const isNoLiveMatch = Boolean(result?.live_unverified);
  const isClarify = Boolean(result?.clarify?.question);
  const rows = result?.comparison_table || [];
  const altRows = result?.no_match?.closest_alternatives || [];

  return (
    <div className="min-h-screen overflow-x-hidden bg-slate-100/70 text-slate-900 antialiased">
      <main className="mx-auto max-w-[1440px] lg:grid lg:h-screen lg:grid-cols-[minmax(340px,0.82fr)_minmax(520px,1.4fr)] lg:overflow-hidden">
        <aside className="border-b border-slate-200 bg-slate-50 px-4 py-5 sm:px-6 lg:h-screen lg:overflow-y-auto lg:border-b-0 lg:border-r lg:px-7 lg:py-6">
          <div className="space-y-5 pb-2">
            <div className="flex items-start justify-between gap-3">
              <BrandHeader compact={hasSession} />
              {(hasSession || conversation.length > 0) && (
                <button
                  type="button"
                  onClick={resetSession}
                  className="mt-0.5 inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-[12px] font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
                >
                  <RotateCcw className="h-3 w-3" aria-hidden="true" />
                  Start new search
                </button>
              )}
            </div>

            <ShoppingConversation
              messages={conversation}
              result={result}
              busy={busy}
              onClarify={refine}
              onSearchOnline={searchOnline}
            />

            <section
              aria-label="Message Pickly"
              className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
            >
              {result && !isClarify && hasSession && (
                <div className="mb-3 border-b border-slate-100 pb-3">
                  <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-slate-400">
                    Quick refinements
                  </p>
                  <RefineActions
                    understood={result.understood}
                    onRefine={refine}
                    disabled={!!busy}
                  />
                </div>
              )}

              <label htmlFor="query" className="mb-2 block text-sm font-semibold text-slate-900">
                {hasSession ? "Tell Pickly what you'd change" : 'What are you shopping for?'}
              </label>
              <Textarea
                id="query"
                value={transcript}
                onChange={(event) => setTranscript(event.target.value)}
                placeholder={hasSession
                  ? "Tell Pickly what you'd change…"
                  : "I'm looking for…"}
                className="min-h-[64px] resize-none border-slate-200 bg-slate-50/70 text-[14px] focus-visible:ring-slate-400"
              />

              <div className="mt-3 flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <MicRecorder onAudio={handleAudio} disabled={!!busy} />
                  {busy === 'transcribing' && (
                    <span className="flex items-center gap-1.5 text-[12px] text-slate-500">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> Transcribing…
                    </span>
                  )}
                </div>
                <Button
                  onClick={() => runDiscovery()}
                  disabled={!transcript.trim() || !!busy}
                  className="h-9 shrink-0 gap-2 rounded-lg bg-slate-900 px-4 text-[13px] font-medium text-white hover:bg-slate-800"
                >
                  {busy === 'running'
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    : <Search className="h-3.5 w-3.5" />}
                  {busy === 'running' ? 'Updating…' : hasSession ? 'Send' : 'Search'}
                </Button>
              </div>

              {!hasSession && !busy && (
                <div className="mt-3 border-t border-slate-100 pt-3">
                  <p className="mb-2 text-[11.5px] text-slate-500">Try asking</p>
                  <div className="flex flex-wrap gap-1.5">
                    {[
                      'Soft Twin XL comforter under $50',
                      'Work backpack under $60',
                      'STEM toy under $30',
                    ].map((example) => (
                      <button
                        key={example}
                        type="button"
                        disabled={!!busy}
                        onClick={() => runDiscovery(example)}
                        className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left text-[12px] leading-snug text-slate-600 hover:border-slate-300 hover:bg-slate-100 hover:text-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:opacity-50"
                      >
                        {example}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {error && (
                <div className="mt-3 flex items-start gap-2 rounded-lg bg-red-50 p-3 text-[12px] text-red-800">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span className="break-words">{error}</span>
                </div>
              )}

              <div className="mt-3 flex justify-end border-t border-slate-100 pt-3">
                <CatalogBadge catalog={catalog} />
              </div>
            </section>

            <YourSearch
              session={session}
              onRemoveFeature={removeFeature}
              onSetPriority={setPriority}
              onRemoveColor={removeColor}
            />

            <div className="hidden lg:block">
              <AssistantOutcome
                result={result}
                busy={busy}
                ttsUrl={ttsUrl}
                onPlay={playTts}
                voiceTranscript={lastVoiceTranscript}
              />
            </div>
          </div>
        </aside>

        <section
          aria-label="Product results"
          className="min-w-0 bg-white px-4 py-6 sm:px-6 lg:h-screen lg:overflow-y-auto lg:px-8 lg:py-7"
        >
          <div className="mx-auto max-w-4xl space-y-5 pb-8">
            {busy === 'running' && (
              <div className="flex min-h-[300px] items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50/70">
                <p className="flex items-center gap-2 text-[14px] text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Finding the best matches…
                </p>
              </div>
            )}

            {!result && !busy && (
              <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50/60 px-8 text-center">
                <div className="max-w-sm">
                  <span className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-blue-50 text-blue-700">
                    <ShoppingBag className="h-5 w-5" aria-hidden="true" />
                  </span>
                  <h2 className="mt-4 text-[20px] font-semibold tracking-tight text-slate-900">
                    Your best matches will appear here
                  </h2>
                  <p className="mt-2 text-[14px] leading-relaxed text-slate-500">
                    Tell Pickly what matters on the left, then browse grounded products here.
                  </p>
                </div>
              </div>
            )}

            {result && !busy && (
              <>
                {isNoLiveMatch && (
                  <NoLiveMatchState
                    liveUnverified={result.live_unverified}
                    budget={result.understood?.budget}
                    onRaiseBudget={raiseBudget}
                    onReset={resetSession}
                  />
                )}

                {isNoMatch && (
                  <NoMatchState
                    noMatch={result.no_match}
                    onRaiseBudget={raiseBudget}
                    onSearchOnline={searchOnline}
                  />
                )}

                {!isNoMatch && rows.length > 0 && (
                  <ProductResults
                    rows={rows}
                    requested={result.understood?.qualitative_features || []}
                    reconciliation={result.reconciliation}
                  />
                )}

                {(result.related_online || []).length > 0 && (
                  <ProductResults rows={result.related_online} variant="related" />
                )}

                {isNoMatch && altRows.length > 0 && (
                  <ProductResults rows={altRows} variant="alternatives" />
                )}

                {!isNoMatch && rows.length > 1 && (
                  <ComparisonTable rows={rows} reconciliation={result.reconciliation} />
                )}
              </>
            )}
          </div>
        </section>

        <div className="space-y-4 px-4 pb-8 sm:px-6 lg:hidden">
          <AssistantOutcome
            result={result}
            busy={busy}
            ttsUrl={ttsUrl}
            onPlay={playTts}
            voiceTranscript={lastVoiceTranscript}
          />
        </div>

        {ttsUrl && (
          <audio
            controls
            autoPlay
            src={ttsUrl}
            className="mx-4 mb-4 h-9 w-[calc(100%-2rem)] sm:mx-6 sm:w-[calc(100%-3rem)] lg:mx-0 lg:w-full lg:px-6"
          />
        )}
      </main>
    </div>
  );
}
