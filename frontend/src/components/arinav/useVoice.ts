import { useCallback, useEffect, useRef, useState } from 'react';
import { stopAllPlayback, VoiceIO } from '../../lib/voice';

/** Push-to-talk and reply playback for the chat; callbacks may change between renders. */
export function useVoice(handlers: { onAudio: (wav: string) => void; onError: (message: string) => void }) {
  const io = useRef<VoiceIO | null>(null);
  const latest = useRef(handlers);
  latest.current = handlers;
  const [recording, setRecording] = useState(false);
  const [playing, setPlaying] = useState(false);

  useEffect(
    () => () => {
      io.current?.dispose();
      io.current = null;
    },
    [],
  );

  const voice = () => (io.current ??= new VoiceIO());

  const stop = useCallback(() => {
    const result = io.current?.stop() ?? null;
    setRecording(false);
    if (!result) return;
    if (result.wav) latest.current.onAudio(result.wav);
    else latest.current.onError('Recording too short. Hold the button while you speak.');
  }, []);

  const start = useCallback(async () => {
    // Silence every reply and guidance hint first: the microphone must never record our speech.
    stopAllPlayback();
    try {
      const started = await voice().start(stop);
      setRecording(started && voice().isRecording);
    } catch {
      setRecording(false);
      latest.current.onError('Microphone access failed. Allow the microphone for this page and try again.');
    }
  }, [stop]);

  const play = useCallback(async (pcm: string, rate: number) => {
    setPlaying(true);
    try {
      await voice().play(pcm, rate);
    } catch {
      latest.current.onError('Could not play the spoken reply; the text above is complete.');
    } finally {
      setPlaying(false);
    }
  }, []);

  const stopPlayback = useCallback(() => {
    io.current?.stopPlayback();
    setPlaying(false);
  }, []);

  return { recording, playing, start, stop, play, stopPlayback };
}
