import React, { useEffect, useRef } from 'react';
import { Globe, MessageCircleQuestion } from 'lucide-react';

// Quick answers to the clarifying question. All four answer the "who is it
// for" half, which holds for every product: a backpack, a comforter and a
// puzzle can each be for a child or a gift. Use-case answers cannot be fixed
// the same way ("travel" fits a backpack, not a comforter), so that half is
// left to the free-text box the prompt above points to.
//
// Each choice carries the constraint field it answers, and the click applies
// that value directly rather than sending prose back through the Router: the
// shopper already made an explicit choice, and re-extracting it from a
// sentence can fail and re-ask the question they just answered.
const CLARIFY_CHOICES = [
  { label: 'for me', field: 'audience', value: 'adult' },
  { label: 'for a child', field: 'audience', value: 'child' },
  { label: 'for a family', field: 'audience', value: 'family' },
  { label: 'as a gift', field: 'audience', value: 'gift' },
];

/**
 * Presentation-only shopping history. SearchContext remains the decision
 * state; these rows preserve only shopper turns and short acknowledgements.
 */
export default function ShoppingConversation({
  messages,
  result,
  busy,
  onClarify,
  onSearchOnline,
}) {
  const historyRef = useRef(null);
  const clarification = result?.clarify?.question;
  const noMatch = Boolean(result?.no_match);

  useEffect(() => {
    const history = historyRef.current;
    if (history) history.scrollTop = history.scrollHeight;
  }, [messages.length, busy, clarification, noMatch]);

  if (!messages.length) return null;

  return (
    <section aria-labelledby="conversation-heading">
      <h2
        id="conversation-heading"
        className="mb-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400"
      >
        Conversation
      </h2>

      <div
        ref={historyRef}
        className="max-h-[320px] overflow-y-auto overscroll-contain pr-1"
      >
        <ol className="space-y-3.5" aria-label="Shopping conversation">
          {messages.map((message, index) => (
            <li
              key={`${message.role}-${index}`}
              className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div className="w-fit max-w-[88%]">
                <div
                  className={`flex items-center gap-2 ${
                    message.role === 'user' ? 'justify-end' : ''
                  }`}
                >
                  <span className={`text-[10.5px] font-semibold uppercase tracking-[0.12em] ${
                    message.role === 'user' ? 'text-slate-500' : 'text-emerald-700'
                  }`}>
                    {message.role === 'user' ? 'You' : 'Pickly'}
                  </span>
                  {message.role === 'user' && message.viaVoice && (
                    <span className="text-[10.5px] text-slate-400">
                      via voice · Whisper
                    </span>
                  )}
                </div>

                {message.role === 'user' ? (
                  <p className="mt-1 rounded-2xl rounded-tr-sm border border-slate-200 bg-slate-100 px-3.5 py-2.5 text-[13.5px] leading-relaxed text-slate-800">
                    {message.text}
                  </p>
                ) : (
                  <p className="mt-1 rounded-2xl rounded-tl-sm border border-emerald-100 bg-emerald-50/70 px-3.5 py-2.5 text-[13.5px] leading-relaxed text-slate-700 shadow-[inset_2px_0_0_rgb(110,231,183)]">
                    {message.text}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>

        {busy === 'running' && (
          <div className="mt-3 flex justify-start">
            <div className="w-fit max-w-[88%]">
              <p className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-emerald-700">
                Pickly
              </p>
              <p className="mt-1 rounded-2xl rounded-tl-sm border border-emerald-100 bg-emerald-50/70 px-3.5 py-2.5 text-[12.5px] text-slate-600">
                Updating your search…
              </p>
            </div>
          </div>
        )}

        {clarification && busy !== 'running' && (
          <div className="ml-1 mt-3 border-l border-blue-200 pl-3">
            <div className="flex items-start gap-2 text-[12.5px] text-slate-600">
              <MessageCircleQuestion className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" />
              <span>Choose a quick answer, or type your own below.</span>
            </div>
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {CLARIFY_CHOICES.map((choice) => (
                <button
                  key={choice.label}
                  type="button"
                  onClick={() => onClarify(choice)}
                  className="rounded-lg border border-blue-200 bg-white px-2.5 py-1.5 text-[12px] font-medium text-slate-700 hover:bg-blue-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                >
                  {choice.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {noMatch && busy !== 'running' && onSearchOnline && (
          <div className="ml-1 mt-3 border-l border-amber-200 pl-3">
            <button
              type="button"
              onClick={onSearchOnline}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-[12.5px] font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
            >
              <Globe className="h-3.5 w-3.5" aria-hidden="true" />
              Search online
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
