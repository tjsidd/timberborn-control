import { Activity, AlertTriangle, CheckCircle2, Power, RefreshCw, Settings2, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { createSession, deleteSensor, fetchStatus, loadSession, refreshStatus, saveConfiguration, setAutoDeleteMissingSensors, setLever } from "./api";
import { ConfigurationView } from "./ConfigurationView";
import { OverviewView } from "./OverviewView";
import { SessionView } from "./SessionView";
import type { Configuration, Snapshot } from "./types";

const emptySnapshot: Snapshot = {
  active_session_id: "",
  session_name: "Loading...",
  sessions: [],
  connection: {
    ok: false,
    base_url: "http://localhost:8080",
    last_error: null,
    last_seen_at: null
  },
  auto_delete_missing_sensors: false,
  graph_positions: {},
  config_error: null,
  automation_error: null,
  event_log_error: null,
  levers: [],
  adapters: [],
  tanks: [],
  gates: [],
  sensors: [],
  rules: [],
  last_rule_evaluations: [],
  decisions: [],
  events: []
};

export function App() {
  const [snapshot, setSnapshot] = useState<Snapshot>(emptySnapshot);
  const [loading, setLoading] = useState(false);
  const [commanding, setCommanding] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deletingSensor, setDeletingSensor] = useState<string | null>(null);
  const [savingSensorSettings, setSavingSensorSettings] = useState(false);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [view, setView] = useState<"overview" | "status" | "configuration" | "session">("overview");
  const [error, setError] = useState<string | null>(null);

  async function loadStatus(refresh = false) {
    setLoading(true);
    if (refresh) setError(null);
    try {
      const next = refresh ? await refreshStatus() : await fetchStatus();
      setSnapshot(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadStatus();
    const timer = window.setInterval(() => void loadStatus(), 2500);
    return () => window.clearInterval(timer);
  }, []);

  const activeAdapters = useMemo(
    () => snapshot.sensors.filter((sensor) => sensor.state === true).length,
    [snapshot.sensors]
  );
  const sensorErrors = snapshot.sensors.filter((sensor) => sensor.error);

  async function toggleLever(name: string, state: boolean) {
    setCommanding(name);
    setError(null);
    try {
      await setLever(name, !state);
      await loadStatus(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setCommanding(null);
    }
  }

  async function saveConfig(config: Configuration): Promise<boolean> {
    setSaving(true);
    setError(null);
    try {
      setSnapshot(await saveConfiguration(config));
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown save error");
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function removeSensor(name: string) {
    setDeletingSensor(name);
    setError(null);
    try {
      setSnapshot(await deleteSensor(name));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete sensor");
    } finally {
      setDeletingSensor(null);
    }
  }

  async function toggleAutoDeleteMissingSensors(enabled: boolean) {
    setSavingSensorSettings(true);
    setError(null);
    try {
      setSnapshot(await setAutoDeleteMissingSensors(enabled));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update sensor settings");
    } finally {
      setSavingSensorSettings(false);
    }
  }

  async function changeSession(action: () => Promise<Snapshot>): Promise<boolean> {
    setSessionBusy(true);
    setError(null);
    try {
      setSnapshot(await action());
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change session");
      return false;
    } finally {
      setSessionBusy(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Timberborn Control</p>
          <h1>{snapshot.session_name}</h1>
        </div>
        <button className="icon-button" onClick={() => void loadStatus(true)} disabled={loading}>
          <RefreshCw size={18} aria-hidden="true" />
          <span>Refresh</span>
        </button>
      </header>

      <nav className="view-tabs" aria-label="Views">
        <button className={view === "overview" ? "selected" : ""} onClick={() => setView("overview")}>Overview</button>
        <button className={view === "status" ? "selected" : ""} onClick={() => setView("status")}>Status</button>
        <button className={view === "configuration" ? "selected" : ""} onClick={() => setView("configuration")}>Configuration</button>
        <button className={view === "session" ? "selected" : ""} onClick={() => setView("session")}>Session</button>
      </nav>

      <section className="status-strip">
        <StatusTile
          label="Game API"
          value={snapshot.connection.ok ? "Connected" : "Offline"}
          tone={snapshot.connection.ok ? "good" : "warn"}
          icon={snapshot.connection.ok ? <CheckCircle2 size={20} /> : <AlertTriangle size={20} />}
        />
        <StatusTile label="Levers" value={String(snapshot.levers.length)} icon={<Power size={20} />} />
        <StatusTile label="Active sensors" value={`${activeAdapters}/${snapshot.sensors.length}`} icon={<Activity size={20} />} />
        <StatusTile label="Rules" value={String(snapshot.rules.length)} icon={<Settings2 size={20} />} />
      </section>

      {error ? <p className="error-line" role="alert">{error}</p> : null}
      {snapshot.config_error ? <p className="error-line" role="alert">{snapshot.config_error}</p> : null}
      {snapshot.automation_error ? <p className="error-line" role="alert">{snapshot.automation_error}</p> : null}
      {snapshot.event_log_error ? <p className="error-line" role="alert">{snapshot.event_log_error}</p> : null}
      {!snapshot.connection.ok && snapshot.connection.last_error ? (
        <p className="error-line" role="alert">Game API at {snapshot.connection.base_url}: {snapshot.connection.last_error}</p>
      ) : null}
      {sensorErrors.length ? <div className="error-line" role="alert"><strong>Sensor state unavailable</strong>
        {sensorErrors.map((sensor) => <p key={sensor.adapter_name}>{sensor.error} {sensor.last_seen_at ? `Last seen ${new Date(sensor.last_seen_at).toLocaleString()}.` : ""}</p>)}
      </div> : null}

      {view === "session" ? <SessionView snapshot={snapshot} busy={sessionBusy}
        onCreate={(name) => changeSession(() => createSession(name))}
        onLoad={(id) => changeSession(() => loadSession(id))} />
        : view === "configuration" ? <ConfigurationView snapshot={snapshot} onSave={saveConfig} busy={saving} />
        : view === "overview" ? <OverviewView snapshot={snapshot} onSave={saveConfig} busy={saving} error={error} /> : <section className="workspace">
        <Panel title="Levers">
          {snapshot.levers.length ? (
            <div className="table">
              {snapshot.levers.map((lever) => (
                <div className="row" key={lever.name}>
                  <div>
                    <strong>{lever.name}</strong>
                    <span>{lever.springReturn ? "spring return" : "latching"}</span>
                  </div>
                  <button
                    className={lever.state ? "state-button on" : "state-button"}
                    disabled={commanding === lever.name || snapshot.rules.some((rule) => rule.enabled && rule.lever === lever.name)}
                    title={snapshot.rules.some((rule) => rule.enabled && rule.lever === lever.name) ? "Managed by rule" : "Switch lever"}
                    onClick={() => void toggleLever(lever.name, lever.state)}
                  >
                    <Power size={16} aria-hidden="true" />
                    <span>{lever.state ? "On" : "Off"}</span>
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState text="No HTTP levers reported yet." />
          )}
        </Panel>

        <Panel title="Rule decisions">
          {snapshot.decisions.length ? <div className="event-list">{snapshot.decisions.map((decision, index) => (
            <div className="event" key={`${decision.rule}-${index}`}>
              <strong>{decision.rule} · {decision.lever_name ?? "No lever"} → {decision.output_state === null ? "Hold" : decision.output_state ? "On" : "Off"}</strong>
              <p>{decision.reason}</p>
              <span>{Object.entries(decision.inputs).map(([name, state]) => `${name}: ${state === null ? "missing" : state ? "on" : "off"}`).join(" · ") || "No inputs"}</span>
            </div>
          ))}</div> : <EmptyState text="No automation decisions yet." />}
        </Panel>

        <Panel title="Sensors" action={<label className="switch-control">
          <input type="checkbox" checked={snapshot.auto_delete_missing_sensors}
            disabled={savingSensorSettings}
            onChange={(event) => void toggleAutoDeleteMissingSensors(event.target.checked)} />
          <span>Auto Delete Missing Sensors</span>
        </label>}>
          {snapshot.sensors.length ? (
            <div className="table">
              {snapshot.sensors.map((sensor) => (
                <div className="row" key={sensor.adapter_name}>
                  <div>
                    <strong>{sensor.adapter_name}</strong>
                    <span>{sensor.type ?? "Unclassified"}</span>
                    <small className="sensor-time">Last seen {sensor.last_seen_at ? new Date(sensor.last_seen_at).toLocaleString() : "never"}</small>
                  </div>
                  <div className="sensor-actions">
                    <span className={sensor.error ? "pill stale" : sensor.last_state ? "pill active" : "pill"}
                      title={sensor.error ?? "Last known sensor state"}>{sensor.last_state === null ? "Unknown" : sensor.last_state ? "On" : "Off"}</span>
                    <button className="tool-button" aria-label={`Delete sensor ${sensor.adapter_name}`}
                      title={snapshot.connection.ok && sensor.state === null ? "Delete stale sensor" : "Only sensors absent from the game can be deleted"}
                      disabled={!snapshot.connection.ok || sensor.state !== null || deletingSensor !== null}
                      onClick={() => void removeSensor(sensor.adapter_name)}><Trash2 size={17} /></button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState text="No sensors discovered yet." />
          )}
        </Panel>

        <Panel title="Rule evaluations">
          {snapshot.last_rule_evaluations.length ? (
            <div className="table compact">
              {snapshot.last_rule_evaluations.map((evaluation) => (
                <div className="row" key={`${evaluation.rule_id}-${evaluation.evaluated_at}`}>
                  <div>
                    <strong>{evaluation.rule_id}</strong>
                    <span>{evaluation.reason ?? "ready"}</span>
                  </div>
                  <span className={evaluation.matched ? "pill active" : "pill"}>{evaluation.matched ? "Matched" : "No match"}</span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState text="No rules loaded in this run." />
          )}
        </Panel>

        <Panel title="Events">
          {snapshot.events.length ? (
            <div className="event-list">
              {snapshot.events
                .slice()
                .reverse()
                .slice(0, 30)
                .map((event) => (
                  <div className="event" key={`${event.kind}-${event.at}`}>
                    <span>{new Date(event.at).toLocaleString()}</span>
                    <strong>{event.kind}</strong>
                    <p>{event.message}</p>
                  </div>
                ))}
            </div>
          ) : (
            <EmptyState text="No events yet." />
          )}
        </Panel>
      </section>}
    </main>
  );
}

function StatusTile({
  label,
  value,
  icon,
  tone = "neutral"
}: {
  label: string;
  value: string;
  icon: React.ReactNode;
  tone?: "neutral" | "good" | "warn";
}) {
  return (
    <div className={`status-tile ${tone}`}>
      <div className="tile-icon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Panel({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-header"><h2>{title}</h2>{action}</div>
      {children}
    </section>
  );
}

function EmptyState({ text }: { text: string }) {
  return <p className="empty">{text}</p>;
}
