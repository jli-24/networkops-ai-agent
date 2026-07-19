import { useMemo, useRef, useState } from "react";

import { createConsoleClient } from "./api/client";
import { AuthGate } from "./auth/AuthGate";
import { ConsoleShell, type ConsoleView } from "./components/ConsoleShell";
import { DashboardPage } from "./pages/DashboardPage";
import { EvaluationPage } from "./pages/EvaluationPage";
import { GovernancePage } from "./pages/GovernancePage";
import { IncidentsPage } from "./pages/IncidentsPage";
import { PolicyPage } from "./pages/PolicyPage";
import { TracePage } from "./pages/TracePage";

export default function App() {
  const [token, setToken] = useState("");
  const [authMessage, setAuthMessage] = useState<string>();
  const [view, setView] = useState<ConsoleView>("dashboard");
  const [traceIncident, setTraceIncident] = useState("");
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const client = useMemo(
    () => {
      const clientToken = token;
      return createConsoleClient(token, () => {
        if (tokenRef.current !== clientToken) return;
        setToken("");
        setAuthMessage("Authentication required");
        setView("dashboard");
      });
    },
    [token],
  );

  if (!token) {
    return <AuthGate message={authMessage} onConnect={(value) => { setAuthMessage(undefined); setToken(value); }} />;
  }

  let page;
  switch (view) {
    case "incidents":
      page = <IncidentsPage client={client} onOpenTrace={(id) => { setTraceIncident(id); setView("trace"); }} />;
      break;
    case "trace": page = <TracePage client={client} incidentId={traceIncident} />; break;
    case "governance": page = <GovernancePage client={client} />; break;
    case "policy": page = <PolicyPage client={client} />; break;
    case "evaluation": page = <EvaluationPage client={client} />; break;
    default: page = <DashboardPage client={client} />;
  }

  return <ConsoleShell view={view} onNavigate={setView} onDisconnect={() => { setToken(""); setAuthMessage(undefined); }}>{page}</ConsoleShell>;
}
