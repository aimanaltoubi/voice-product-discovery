// Thin API client for the FastAPI backend.
// Replaces the Base44 SDK: the three former `base44.functions.invoke(...)`
// calls (transcribe / runDiscovery / speak) map 1:1 to these endpoints.

const API_BASE = import.meta.env.VITE_API_BASE || '';

async function handle(res) {
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON error body */ }
  if (!res.ok) {
    const msg = body?.detail || body?.error || `${res.status} ${res.statusText}`;
    throw new Error(msg);
  }
  return body;
}

/** POST audio blob -> { transcript, segments, language, provider } */
export async function transcribe(blob) {
  const form = new FormData();
  const ext = (blob.type || 'audio/webm').split('/')[1]?.split(';')[0] || 'webm';
  form.append('audio', blob, `recording.${ext}`);
  const res = await fetch(`${API_BASE}/api/transcribe`, { method: 'POST', body: form });
  return handle(res);
}

/**
 * POST transcript -> full discovery result (steps, spoken_answer, table, citations).
 *
 * `searchContext` carries the active search session. When supplied the request
 * is a refinement: the backend merges this utterance onto the accumulated
 * constraints and searches the whole catalog again with the combined intent.
 */
export async function discover(transcript, searchContext = null, mode = null) {
  const res = await fetch(`${API_BASE}/api/discover`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      transcript,
      mode: mode || (searchContext ? 'refine' : 'new'),
      ...(searchContext ? { search_context: searchContext } : {})
    })
  });
  return handle(res);
}

/** POST text -> { audio_url } (fragment-based TTS: full file, then play) */
export async function speak(text) {
  const res = await fetch(`${API_BASE}/api/speak`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text })
  });
  return handle(res);
}

/** GET provider + catalog status -> { llm, catalog: {data_source, is_real_data, count, ...} } */
export async function health() {
  const res = await fetch(`${API_BASE}/api/health`);
  return handle(res);
}
