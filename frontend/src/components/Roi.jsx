import { useState, useEffect, useCallback } from "react";
import { api } from "../api";

const PRIORITY_COLOR = { high: "#ef4444", medium: "#f59e0b", low: "#10b981" };
const PRIORITY_ORDER = ["high", "medium", "low"];

function formatMinutes(mins) {
  if (mins >= 60) {
    const hours = mins / 60;
    return `${Number.isInteger(hours) ? hours : hours.toFixed(1)}h`;
  }
  return `${mins}m`;
}

function PriorityBars({ data }) {
  const max = Math.max(1, ...data.map((d) => Number(d.count)));
  const present = PRIORITY_ORDER.filter((p) => data.some((d) => d.priority === p));

  return (
    <div className="roi-bars">
      {present.map((p) => {
        const row = data.find((d) => d.priority === p);
        const count = row ? Number(row.count) : 0;
        const pct = Math.max(4, Math.round((count / max) * 100));
        return (
          <div className="roi-bar-row" key={p}>
            <span className="roi-bar-label">{p}</span>
            <div className="roi-bar-track">
              <div
                className="roi-bar-fill"
                style={{ width: `${pct}%`, background: PRIORITY_COLOR[p] }}
              />
            </div>
            <span className="roi-bar-value">{count}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function Roi() {
  const [roi, setRoi] = useState(null);
  const [byPriority, setByPriority] = useState([]);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    Promise.all([api.roiStats(), api.ticketsByPriority()])
      .then(([roiData, priorityData]) => {
        setRoi(roiData);
        setByPriority(priorityData);
        setError(null);
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, [load]);

  if (error) return <div className="chat-error">{error}</div>;
  if (!roi) return <p className="chat-empty">Loading…</p>;

  const deflectionPct = Math.round(roi.deflection_rate * 100);
  const resolvedCount = roi.tickets_auto_resolved_by_kb;

  return (
    <div className="panel roi-dashboard">
      <div className="roi-hero">
        <span className="roi-hero-label">Estimated time saved</span>
        <span className="roi-hero-value">{formatMinutes(roi.estimated_minutes_saved)}</span>
        <span className="roi-hero-sub">
          from {resolvedCount} ticket{resolvedCount === 1 ? "" : "s"} auto-resolved by the knowledge base
        </span>
      </div>

      <div className="roi-kpi-row">
        <div className="roi-tile">
          <span className="roi-tile-label">Tickets created</span>
          <span className="roi-tile-value">{roi.tickets_created}</span>
        </div>
        <div className="roi-tile">
          <span className="roi-tile-label">Auto-resolved by KB</span>
          <span className="roi-tile-value">{roi.tickets_auto_resolved_by_kb}</span>
        </div>
        <div className="roi-tile">
          <span className="roi-tile-label">Deflection rate</span>
          <span className="roi-tile-value">{deflectionPct}%</span>
        </div>
        <div className="roi-tile">
          <span className="roi-tile-label">SLA escalations</span>
          <span className="roi-tile-value">{roi.tickets_escalated_sla_breach}</span>
        </div>
        <div className="roi-tile">
          <span className="roi-tile-label">Digests sent</span>
          <span className="roi-tile-value">{roi.digests_sent}</span>
        </div>
        <div className="roi-tile">
          <span className="roi-tile-label">Marketing drafts</span>
          <span className="roi-tile-value">{roi.marketing_content_generated}</span>
        </div>
      </div>

      <div className="roi-section">
        <h2>Tickets by priority</h2>
        {byPriority.length === 0 ? (
          <p className="chat-empty">No tickets yet.</p>
        ) : (
          <PriorityBars data={byPriority} />
        )}
      </div>

      <p className="roi-footnote">Counters persist in Turso and survive a server restart. Refreshes every 30s.</p>
    </div>
  );
}
