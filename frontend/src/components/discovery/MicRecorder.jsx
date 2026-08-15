import React, { useState, useRef } from 'react';
import { Mic, Square, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';

export default function MicRecorder({ onAudio, disabled }) {
  const [recording, setRecording] = useState(false);
  const [loading, setLoading] = useState(false);
  const mediaRef = useRef(null);
  const chunksRef = useRef([]);

  const start = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      chunksRef.current = [];
      mr.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data);
      };
      mr.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: mr.mimeType || 'audio/webm' });
        stream.getTracks().forEach((t) => t.stop());
        setLoading(true);
        try {
          await onAudio(blob);
        } finally {
          setLoading(false);
        }
      };
      mediaRef.current = mr;
      mr.start();
      setRecording(true);
    } catch (e) {
      alert('Microphone access failed: ' + e.message);
    }
  };

  const stop = () => {
    mediaRef.current?.stop();
    setRecording(false);
  };

  return (
    <Button
      type="button"
      onClick={recording ? stop : start}
      disabled={disabled || loading}
      variant="outline"
      className={[
        'h-9 gap-1.5 rounded-lg px-3 text-[13.5px] font-medium transition-colors',
        recording
          ? 'border-red-300 bg-red-50 text-red-800 hover:bg-red-100'
          : 'border-slate-300 text-slate-800 hover:bg-slate-50',
      ].join(' ')}
    >
      {loading ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : recording ? (
        <Square className="h-3.5 w-3.5 fill-current" />
      ) : (
        <Mic className="h-3.5 w-3.5" />
      )}
      {loading ? 'Transcribing…' : recording ? 'Stop' : 'Record'}
    </Button>
  );
}