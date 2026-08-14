import React, { useState } from 'react';
import { transcribe, discover, speak } from '@/api/client';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Sparkles, Volume2, Loader2, AlertCircle, Mic } from 'lucide-react';
import MicRecorder from '@/components/discovery/MicRecorder';
import AgentStepLog from '@/components/discovery/AgentStepLog';
import ComparisonTable from '@/components/discovery/ComparisonTable';
import CitationList from '@/components/discovery/CitationList';

export default function Home() {
  const [transcript, setTranscript] = useState('');
  const [busy, setBusy] = useState('');
  const [result, setResult] = useState(null);
  const [ttsUrl, setTtsUrl] = useState(null);
  const [error, setError] = useState('');

  const handleAudio = async (blob) => {
    setError('');
    setBusy('transcribing');
    try {
      const res = await transcribe(blob);
      const t = res.transcript || '';
      setTranscript(t);
      // Siri-like hands-free flow: transcribe -> run discovery -> speak automatically.
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

  const runDiscovery = async (text) => {
    const q = (typeof text === 'string' ? text : transcript).trim();
    if (!q) return;
    setError('');
    setBusy('running');
    setTtsUrl(null);
    setResult(null);
    try {
      const res = await discover(q);
      setResult(res);
      // Auto-synthesize + play the spoken answer as soon as it's ready.
      if (res.spoken_answer) await synthesize(res.spoken_answer);
      else setBusy('');
    } catch (e) {
      setError(e.message);
      setBusy('');
    }
  };

  const playTts = () => {
    if (result?.spoken_answer) synthesize(result.spoken_answer);
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-stone-50 to-white">
      <header className="max-w-4xl mx-auto px-6 pt-14 pb-8">
        <div className="flex items-center gap-2 text-emerald-600 mb-3">
          <Sparkles className="w-4 h-4" />
          <span className="text-xs font-medium uppercase tracking-[0.2em]">Agentic Voice Discovery</span>
        </div>
        <h1 className="text-4xl sm:text-5xl font-heading tracking-tight text-foreground leading-tight">
          Agentic Voice-to-Voice AI Assistant for Product Discovery
        </h1>
      </header>

      <main className="max-w-4xl mx-auto px-6 pb-24 space-y-8">
        {/* Input panel */}
        <section className="rounded-2xl border border-border bg-card shadow-sm p-6 sm:p-8">
          <div className="flex flex-col sm:flex-row sm:items-center gap-4 mb-5">
            <MicRecorder onAudio={handleAudio} disabled={!!busy} />
            <span className="text-sm text-muted-foreground">or type your request below</span>
          </div>

          <Textarea
            value={transcript}
            onChange={(e) => setTranscript(e.target.value)}
            placeholder="e.g. Recommend an eco-friendly stainless-steel cleaner under fifteen dollars"
            className="min-h-[88px] resize-none text-base"
          />

          <div className="flex items-center gap-3 mt-4">
            <Button
              onClick={() => runDiscovery()}
              disabled={!transcript.trim() || !!busy}
              className="rounded-full h-11 px-6 gap-2 bg-emerald-600 hover:bg-emerald-700 text-white"
            >
              {busy === 'running' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              {busy === 'running' ? 'Running agents…' : 'Run discovery'}
            </Button>
            {busy === 'transcribing' && (
              <span className="text-sm text-muted-foreground flex items-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin" /> Transcribing audio…
              </span>
            )}
          </div>

          {error && (
            <div className="mt-4 flex items-start gap-2 text-sm text-destructive bg-destructive/5 rounded-lg p-3">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}
        </section>

        {/* Result */}
        {result && (
          <section className="space-y-8">
            {/* Spoken answer */}
            <div className="rounded-2xl border border-emerald-200 bg-emerald-50/40 p-6">
              <div className="flex items-center justify-between gap-4 mb-3">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-emerald-800">Spoken answer</h2>
                <Button
                  onClick={playTts}
                  disabled={busy === 'speaking'}
                  variant="outline"
                  className="rounded-full h-9 px-4 gap-2 border-emerald-300 text-emerald-800 hover:bg-emerald-100"
                >
                  {busy === 'speaking' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Volume2 className="w-4 h-4" />}
                  {busy === 'speaking' ? 'Synthesizing…' : ttsUrl ? 'Replay' : 'Play voice'}
                </Button>
              </div>
              <p className="text-foreground text-lg leading-relaxed">{result.spoken_answer}</p>
              {ttsUrl && (
                <audio controls autoPlay src={ttsUrl} className="mt-4 w-full">
                  Your browser does not support audio playback.
                </audio>
              )}
            </div>

            {/* Comparison table */}
            {result.comparison_table?.length > 0 && (
              <div className="space-y-3">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Comparison
                </h2>
                <ComparisonTable rows={result.comparison_table} topPickId={result.top_pick?.doc_id} />
              </div>
            )}

            {/* Citations */}
            {result.citations?.length > 0 && (
              <div className="space-y-3">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Citations &amp; data lineage
                </h2>
                <div className="rounded-xl border border-border bg-card p-4">
                  <CitationList citations={result.citations} />
                </div>
              </div>
            )}

            {/* Agent step log */}
            {result.steps?.length > 0 && (
              <div className="space-y-3">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Agent step log
                </h2>
                <AgentStepLog steps={result.steps} />
              </div>
            )}
          </section>
        )}

        {!result && !busy && (
          <div className="text-center py-10 text-muted-foreground">
            <Mic className="w-8 h-8 mx-auto mb-3 opacity-40" />
            <p className="text-sm">Record a voice request or type one above, then run discovery.</p>
          </div>
        )}
      </main>
    </div>
  );
}
