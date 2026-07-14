import { useCallback, useEffect, useRef, useState } from "react";
import type { SpeechCtrl } from "@/components/avatar/avatarTypes";

type VoiceState = "idle" | "requesting" | "listening" | "processing" | "error";

function cleanSpeech(text: string): string {
  return text
    .replace(/\bBOS\b/g, "break of structure")
    .replace(/\bCHOCH\b/g, "change of character")
    .replace(/\bVWAP\b/gi, "vee-wap")
    .replace(/\bCVD\b/g, "cumulative delta")
    .replace(/\bRVOL\b/g, "relative volume")
    .replace(/\bATR\b/g, "average true range")
    .replace(/\bR:R\b/gi, "risk to reward")
    .replace(/\bMNQ\b/g, "mini Nasdaq")
    .replace(/\bMGC\b/g, "micro gold")
    .replace(/\bMES\b/g, "micro S and P")
    .replace(/\bMYM\b/g, "micro Dow")
    .slice(0, 500);
}

export function useDashboardV2Voice() {
  const [muted, setMutedState] = useState(() => {
    try {
      return localStorage.getItem("brain_muted") === "1";
    } catch {
      return false;
    }
  });
  const [speaking, setSpeaking] = useState(false);
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [transcript, setTranscript] = useState("");
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const speechCtrlRef = useRef<SpeechCtrl>({ energy: 0, viseme: "rest", active: false });
  const voiceListeningRef = useRef(false);
  const recognitionRef = useRef<{
    start: () => void;
    stop: () => void;
    abort: () => void;
  } | null>(null);
  const energyFrameRef = useRef(0);

  useEffect(() => {
    voiceListeningRef.current = voiceState === "listening";
  }, [voiceState]);

  const stopSpeechAnimation = useCallback(() => {
    cancelAnimationFrame(energyFrameRef.current);
    speechCtrlRef.current = { energy: 0, viseme: "rest", active: false };
    setSpeaking(false);
  }, []);

  const setMuted = useCallback((next: boolean) => {
    try {
      localStorage.setItem("brain_muted", next ? "1" : "0");
    } catch {
      // Storage is optional.
    }
    if (next) {
      window.speechSynthesis?.cancel();
      stopSpeechAnimation();
    }
    setMutedState(next);
  }, [stopSpeechAnimation]);

  const speak = useCallback((text: string) => {
    const synthesis = window.speechSynthesis;
    if (!synthesis || muted || !text.trim()) return;
    synthesis.cancel();
    stopSpeechAnimation();

    const utterance = new SpeechSynthesisUtterance(cleanSpeech(text));
    const voices = synthesis.getVoices();
    const preferred = voices.find((voice) =>
      voice.lang.startsWith("en") && /natural|premium|samantha|google/i.test(voice.name)
    ) ?? voices.find((voice) => voice.lang.startsWith("en")) ?? voices[0];
    if (preferred) utterance.voice = preferred;
    utterance.rate = 0.92;
    utterance.pitch = 1.04;

    utterance.onstart = () => {
      setSpeaking(true);
      speechCtrlRef.current.active = true;
      const startedAt = performance.now();
      const animate = () => {
        if (!speechCtrlRef.current.active) return;
        const phase = (performance.now() - startedAt) / 180;
        speechCtrlRef.current.energy = 0.42 + Math.sin(phase) * 0.22;
        speechCtrlRef.current.viseme = Math.sin(phase * 0.7) > 0 ? "open" : "narrow";
        energyFrameRef.current = requestAnimationFrame(animate);
      };
      energyFrameRef.current = requestAnimationFrame(animate);
    };
    utterance.onend = stopSpeechAnimation;
    utterance.onerror = stopSpeechAnimation;
    speechCtrlRef.current.active = true;
    synthesis.speak(utterance);
  }, [muted, stopSpeechAnimation]);

  const startListening = useCallback((onTranscript: (text: string) => void) => {
    const SpeechRecognition = (
      window as Window & {
        SpeechRecognition?: new () => any;
        webkitSpeechRecognition?: new () => any;
      }
    ).SpeechRecognition ?? (
      window as Window & { webkitSpeechRecognition?: new () => any }
    ).webkitSpeechRecognition;

    if (!SpeechRecognition) {
      setVoiceError("Voice input requires a browser with Speech Recognition support.");
      setVoiceState("error");
      return;
    }

    window.speechSynthesis?.cancel();
    stopSpeechAnimation();
    setVoiceError(null);
    setTranscript("");
    setVoiceState("requesting");

    const recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = "en-US";
    let finalText = "";
    recognitionRef.current = recognition;
    recognition.onstart = () => setVoiceState("listening");
    recognition.onresult = (event: any) => {
      let interim = "";
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const text = String(event.results[index][0]?.transcript ?? "");
        if (event.results[index].isFinal) finalText += text;
        else interim += text;
      }
      setTranscript(finalText + interim);
    };
    recognition.onend = () => {
      const completed = finalText.trim();
      recognitionRef.current = null;
      if (completed) {
        setVoiceState("processing");
        onTranscript(completed);
      } else {
        setVoiceState("idle");
      }
    };
    recognition.onerror = () => {
      recognitionRef.current = null;
      setVoiceError("Microphone input was unavailable. You can still type a question.");
      setVoiceState("error");
    };
    recognition.start();
  }, [stopSpeechAnimation]);

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop();
  }, []);

  const markIdle = useCallback(() => setVoiceState("idle"), []);

  useEffect(() => () => {
    recognitionRef.current?.abort();
    window.speechSynthesis?.cancel();
    cancelAnimationFrame(energyFrameRef.current);
  }, []);

  return {
    muted,
    setMuted,
    speaking,
    speak,
    speechCtrlRef,
    voiceListeningRef,
    voiceState,
    transcript,
    voiceError,
    startListening,
    stopListening,
    markIdle,
  };
}
