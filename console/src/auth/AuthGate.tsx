import { type FormEvent, useState } from "react";

interface AuthGateProps {
  message?: string;
  onConnect: (token: string) => void;
}

export function AuthGate({ message, onConnect }: AuthGateProps) {
  const [value, setValue] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    const token = value.trim();
    if (token) onConnect(token);
  }

  return (
    <main className="auth-screen">
      <section className="auth-card">
        <div className="brand-mark" aria-hidden="true">NO</div>
        <p className="eyebrow">SECURE OPERATOR ACCESS</p>
        <h1>NetworkOps Console</h1>
        <p className="muted">Connect with a short-lived JWT. Credentials remain only in this browser tab's React memory.</p>
        {message && <div className="auth-alert" role="alert">{message}</div>}
        <form onSubmit={submit}>
          <label htmlFor="jwt">JWT access token</label>
          <input
            id="jwt"
            type="password"
            autoComplete="off"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="Paste Bearer token"
          />
          <button className="primary" type="submit" disabled={!value.trim()}>
            Connect console
          </button>
        </form>
        <p className="security-note">Nothing is persisted after the page closes or reloads.</p>
      </section>
    </main>
  );
}
