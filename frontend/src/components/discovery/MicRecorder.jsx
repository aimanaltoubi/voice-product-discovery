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
      variant={recording ? 'destructive' : 'default'}
      className="rounded-full h-12 px-6 gap-2"
    >
      {loading ? (
        <Loader2 className="w-4 h-4 animate-spin" />
      ) : recording ? (
        <Square className="w-4 h-4 fill-current" />
      ) : (
        <Mic className="w-4 h-4" />
      )}
      {loading ? 'Transcribing…' : recording ? 'Stop recording' : 'Record voice'}
    </Button>
  );
}