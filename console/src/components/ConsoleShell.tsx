import type { ReactNode } from "react";

export type ConsoleView = "dashboard" | "incidents" | "trace" | "governance" | "policy" | "evaluation" | "embedded";

const navigation: Array<{ id: ConsoleView; label: string; glyph: string }> = [
  { id: "dashboard", label: "Dashboard", glyph: "▦" },
  { id: "incidents", label: "Incidents", glyph: "◆" },
  { id: "trace", label: "Trace", glyph: "⌁" },
  { id: "governance", label: "Governance", glyph: "◎" },
  { id: "policy", label: "Policy", glyph: "◇" },
  { id: "evaluation", label: "Evaluation", glyph: "◫" },
  { id: "embedded", label: "Embedded Lab", glyph: "⬡" },
];

export function ConsoleShell({ view, onNavigate, onDisconnect, children }: {
  view: ConsoleView;
  onNavigate: (view: ConsoleView) => void;
  onDisconnect: () => void;
  children: ReactNode;
}) {
  return (
    <div className="console-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark small">NO</div><div><strong>NetworkOps</strong><span>OPERATOR CONSOLE</span></div></div>
        <nav aria-label="Console navigation">
          {navigation.map((item) => (
            <button key={item.id} className={view === item.id ? "active" : ""} onClick={() => onNavigate(item.id)} type="button">
              <span aria-hidden="true">{item.glyph}</span>{item.label}
            </button>
          ))}
        </nav>
        <div className="connection-panel"><span className="live-dot" />API SESSION ACTIVE</div>
        <button className="disconnect" type="button" onClick={onDisconnect}>Disconnect</button>
      </aside>
      <main className="workspace">
        <header className="topbar"><div><span className="eyebrow">ENTERPRISE OPERATIONS</span><strong>{navigation.find((item) => item.id === view)?.label}</strong></div><div className="utc-clock">READ-ONLY PROJECTION</div></header>
        <div className="page-content">{children}</div>
      </main>
    </div>
  );
}
