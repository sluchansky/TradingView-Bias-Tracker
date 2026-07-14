import { useState } from "react";

export function DashboardV2Login({
  authenticate,
}: {
  authenticate: (password: string) => Promise<boolean>;
}) {
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="dv2-login">
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!password.trim() || submitting) return;
          setSubmitting(true);
          setError(null);
          try {
            const accepted = await authenticate(password);
            if (!accepted) setError("Unable to authenticate with that password.");
          } catch {
            setError("Unable to authenticate with that password.");
          } finally {
            setSubmitting(false);
          }
        }}
      >
        <span className="dv2-brand-mark">AI</span>
        <h1>AI Trading Partner</h1>
        <p>Connect to open Dashboard V2.</p>
        <input
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="Dashboard password"
          autoFocus
          aria-label="Dashboard password"
        />
        {error && <div className="dv2-login-error">{error}</div>}
        <button type="submit" disabled={submitting}>
          {submitting ? "Connecting…" : "Connect"}
        </button>
        <a href="/">Return to current dashboard</a>
      </form>
    </div>
  );
}
