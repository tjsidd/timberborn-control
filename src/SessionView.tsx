import { FolderOpen, Plus } from "lucide-react";
import { useState } from "react";

import type { Snapshot } from "./types";

type Props = {
  snapshot: Snapshot;
  busy: boolean;
  onCreate: (name: string) => Promise<boolean>;
  onLoad: (id: string) => Promise<boolean>;
};

export function SessionView({ snapshot, busy, onCreate, onLoad }: Props) {
  const [name, setName] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const loadId = selectedId || snapshot.active_session_id;

  async function submitCreate(event: React.FormEvent) {
    event.preventDefault();
    if (await onCreate(name.trim())) {
      setName("");
      setSelectedId("");
    }
  }

  async function submitLoad(event: React.FormEvent) {
    event.preventDefault();
    if (await onLoad(loadId)) setSelectedId("");
  }

  return <div className="session-layout">
    <section className="config-section">
      <h2>Create</h2>
      <form className="session-form" onSubmit={(event) => void submitCreate(event)}>
        <label>Session name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={64} required /></label>
        <button className="command-button" disabled={busy || !name.trim()}><Plus size={17} /> Create session</button>
      </form>
    </section>
    <section className="config-section">
      <h2>Load</h2>
      <form className="session-form" onSubmit={(event) => void submitLoad(event)}>
        <label>Session<select value={loadId} onChange={(event) => setSelectedId(event.target.value)}>
          {snapshot.sessions.map((session) => <option value={session.id} key={session.id}>{session.name}{session.id === snapshot.active_session_id ? " (current)" : ""}</option>)}
        </select></label>
        <button className="command-button" disabled={busy || !loadId || loadId === snapshot.active_session_id}><FolderOpen size={17} /> Load session</button>
      </form>
    </section>
  </div>;
}
