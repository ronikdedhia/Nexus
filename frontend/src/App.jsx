import { useState } from "react";
import Chat from "./components/Chat";
import Tickets from "./components/Tickets";
import Employees from "./components/Employees";
import "./App.css";

const TABS = [
  { key: "chat", label: "Chat" },
  { key: "tickets", label: "Tickets" },
  { key: "employees", label: "Employees" },
];

function App() {
  const [tab, setTab] = useState("chat");

  return (
    <div className="app">
      <header className="app-header">
        <h1>Nexus</h1>
        <p className="app-subtitle">Your workplace assistant for HR, IT and company data. Ask anything, act instantly.</p>
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
        {tab === "employees" && <Employees />}
      </main>

      <footer className="app-footer">Demo data. Answers are AI-generated and can be wrong.</footer>
    </div>
  );
}

export default App;
