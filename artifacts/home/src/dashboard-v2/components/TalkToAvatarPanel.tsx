import { useCallback, useState } from "react";
import type { DashboardMessage } from "../types";
import { DashboardPanel } from "./Panel";

let messageId = 0;

export function TalkToAvatarPanel({
  title = "Talk to Avatar",
  askAssistant,
  speak,
  voiceState,
  transcript,
  voiceError,
  startListening,
  stopListening,
  markIdle,
}: {
  title?: string;
  askAssistant: (question: string) => Promise<string>;
  speak: (text: string) => void;
  voiceState: "idle" | "requesting" | "listening" | "processing" | "error";
  transcript: string;
  voiceError: string | null;
  startListening: (onTranscript: (text: string) => void) => void;
  stopListening: () => void;
  markIdle: () => void;
}) {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<DashboardMessage[]>([]);
  const [asking, setAsking] = useState(false);

  const submit = useCallback(async (raw: string) => {
    const question = raw.trim();
    if (!question || asking) return;
    setInput("");
    setAsking(true);
    setMessages((current) => [...current, { id: ++messageId, role: "user", text: question }]);
    try {
      const answer = await askAssistant(question);
      setMessages((current) => [...current, { id: ++messageId, role: "assistant", text: answer }]);
      speak(answer);
    } catch (error) {
      const text = error instanceof Error ? error.message : "The assistant is unavailable.";
      setMessages((current) => [...current, { id: ++messageId, role: "assistant", text }]);
    } finally {
      setAsking(false);
      markIdle();
    }
  }, [askAssistant, asking, markIdle, speak]);

  return (
    <DashboardPanel title={title} eyebrow="Voice & text">
      <div className="dv2-chat-log" aria-live="polite">
        {messages.length === 0 ? (
          <p>Ask what the AI sees, what is missing, or what would invalidate the current idea.</p>
        ) : messages.slice(-4).map((message) => (
          <div key={message.id} className={`dv2-message is-${message.role}`}>
            <strong>{message.role === "user" ? "You" : "Avatar"}</strong>
            <span>{message.text}</span>
          </div>
        ))}
      </div>
      {(transcript || voiceError) && (
        <div className={voiceError ? "dv2-voice-error" : "dv2-transcript"}>
          {voiceError ?? transcript}
        </div>
      )}
      <form
        className="dv2-chat-form"
        onSubmit={(event) => {
          event.preventDefault();
          void submit(input);
        }}
      >
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Ask about the live market read…"
          aria-label="Question for the AI avatar"
        />
        <button
          type="button"
          className={voiceState === "listening" ? "is-listening" : ""}
          onClick={() => {
            if (voiceState === "listening") stopListening();
            else startListening((text) => void submit(text));
          }}
          disabled={voiceState === "requesting" || voiceState === "processing"}
        >
          {voiceState === "listening" ? "Stop mic" : "Talk"}
        </button>
        <button type="submit" disabled={asking || !input.trim()}>
          {asking ? "Thinking…" : "Ask"}
        </button>
      </form>
    </DashboardPanel>
  );
}
