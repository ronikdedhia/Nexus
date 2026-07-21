import { useState, useEffect } from "react";
import Chat from "./components/Chat";
import Employees from "./components/Employees";
import Tickets from "./components/Tickets";
import Roi from "./components/Roi";
import { api } from "./api";
import "./App.css";

const TABS = [
  { key: "chat", label: "Chat" },
  { key: "employees", label: "Employees" },
  { key: "tickets", label: "Tickets" },
  { key: "roi", label: "ROI" },
];

function StatsBar() {
  const [stats, setStats] = useState(null);

  useEffect(() => {
    api.stats().then(setStats).catch(() => {});
  }, []);

  if (!stats) return null;

  return (
    <div className="stats-bar">
      <span>{stats.total_employees} employees</span>
      <span>{stats.open_tickets} open tickets</span>
      <span>{stats.avg_leave_balance} avg leave days</span>
    </div>
  );
}

function App() {
  const [tab, setTab] = useState("chat");

  return (
    <div className="app">
      <header className="app-header">
        <h1>Nexus</h1>
        <p className="app-subtitle">Ask anything. Act instantly.</p>
        <StatsBar />
      </header>

      <nav className="tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`tab ${tab === t.key ? "tab--active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main className="app-main">
        {tab === "chat" && <Chat />}
        {tab === "employees" && <Employees />}
        {tab === "tickets" && <Tickets />}
        {tab === "roi" && <Roi />}
      </main>
    </div>
  );
}

export default App;
