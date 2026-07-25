import { useState } from "react";
import Chat from "./components/Chat";
import Tickets from "./components/Tickets";
import "./App.css";

const TABS = [
  { key: "chat", label: "Chat" },
  { key: "tickets", label: "Tickets" },
];

function App() {
  const [tab, setTab] = useState("chat");

  return (
    <div className="app">
      <header className="app-header">
        <h1>Nexus</h1>
        <p className="app-subtitle">Ask anything. Act instantly.</p>
        <p className="app-subtitle">All company docs have been ingested here — ask any question.</p>
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
        {tab === "tickets" && <Tickets />}
      </main>
    </div>
  );
}

export default App;
