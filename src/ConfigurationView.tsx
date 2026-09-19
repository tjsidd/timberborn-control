import { AlertTriangle, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import type { Configuration, Gate, GateType, Rule, SensorRole, SensorType, Snapshot, TankDirection } from "./types";

type Props = { snapshot: Snapshot; onSave: (config: Configuration) => Promise<boolean>; busy: boolean };

const sensorTypes: SensorType[] = ["Depth", "Resource", "Flow", "BadWater"];
const gateTypes: GateType[] = ["1m", "2m", "3m"];
const sensorRoles: Record<SensorType, SensorRole[]> = {
  Depth: ["needs_water", "full", "reserve"], Resource: [], Flow: ["heavy_flow"], BadWater: ["badwater"]
};
const configOf = (snapshot: Snapshot): Configuration => ({
  auto_delete_missing_sensors: snapshot.auto_delete_missing_sensors,
  graph_positions: snapshot.graph_positions,
  tanks: snapshot.tanks,
  gates: snapshot.gates,
  sensors: snapshot.sensors.map(({ adapter_name, type, role, tank_id, last_state, last_seen_at }) => ({ adapter_name, type, role, tank_id, last_state, last_seen_at })),
  rules: snapshot.rules
});

export function ConfigurationView({ snapshot, onSave, busy }: Props) {
  const [tankName, setTankName] = useState("");
  const [tankPriority, setTankPriority] = useState("");
  const [tankDirection, setTankDirection] = useState<TankDirection | "">("");
  const [tankParent, setTankParent] = useState("");
  const [editingTank, setEditingTank] = useState<string | null>(null);
  const [gateName, setGateName] = useState("");
  const [gateType, setGateType] = useState<GateType>("1m");
  const [gateTank, setGateTank] = useState("");
  const [gateDestination, setGateDestination] = useState("");
  const [gateLever, setGateLever] = useState("");
  const [editingGate, setEditingGate] = useState<string | null>(null);
  const [openWhenOn, setOpenWhenOn] = useState(true);
  const [editingRule, setEditingRule] = useState<string | null>(null);
  const [rule, setRule] = useState<Rule>(emptyRule());

  useEffect(() => {
    if (editingRule) {
      const current = snapshot.rules.find((item) => item.id === editingRule);
      if (current) setRule(current);
    }
  }, [editingRule]);

  async function update(changes: Partial<Configuration>) {
    return onSave({ ...configOf(snapshot), ...changes });
  }

  async function addTank(event: React.FormEvent) {
    event.preventDefault();
    if (!tankName.trim()) return;
    const tank = { id: editingTank ?? crypto.randomUUID(), name: tankName.trim(), priority: tankPriority === "" ? null : Number(tankPriority), direction: tankDirection || null, parent_tank_id: tankParent || null };
    const tanks = editingTank ? snapshot.tanks.map((item) => item.id === editingTank ? tank : item) : [...snapshot.tanks, tank];
    if (await update({ tanks })) { setTankName(""); setTankPriority(""); setTankDirection(""); setTankParent(""); setEditingTank(null); }
  }

  async function addGate(event: React.FormEvent) {
    event.preventDefault();
    const gate: Gate = {
      id: crypto.randomUUID(), name: gateName.trim(), type: gateType,
      open_when_on: openWhenOn,
      tank_id: gateTank || null, destination_tank_id: gateDestination || null,
      lever_name: gateLever || null
    };
    if (!gate.name) return;
    gate.id = editingGate ?? gate.id;
    const gates = editingGate ? snapshot.gates.map((item) => item.id === editingGate ? gate : item) : [...snapshot.gates, gate];
    if (await update({ gates })) { setGateName(""); setOpenWhenOn(true); setEditingGate(null); }
  }

  async function saveRule(event: React.FormEvent) {
    event.preventDefault();
    if (!rule.id.trim()) return;
    const next = snapshot.rules.filter((item) => item.id !== editingRule);
    if (await update({ rules: [...next, { ...rule, id: rule.id.trim(), lever: rule.lever.trim() }] })) {
      setEditingRule(null);
      setRule(emptyRule());
    }
  }

  function changeCondition(name: string, expected: boolean, previous?: string) {
    const conditions = { ...rule.when_adapters };
    if (previous) delete conditions[previous];
    if (name) conditions[name] = expected;
    setRule({ ...rule, when_adapters: conditions });
  }

  const ruleConditions = Object.entries(rule.when_adapters);
  const availableConditionNames = [...new Set([...snapshot.sensors.map((sensor) => sensor.adapter_name), ...Object.keys(rule.when_adapters)])];
  const availableLeverNames = [...new Set([...snapshot.levers.map((lever) => lever.name), ...(rule.lever ? [rule.lever] : [])])];

  return (
    <div className="config-layout">
      <section className="config-section">
        <h2>Tanks</h2>
        <div className="item-list">
          {snapshot.tanks.map((tank) => (
            <div className="config-item" key={tank.id}>
              <div><strong>{tank.name}</strong><small>{tank.priority === null ? "No priority" : `Priority ${tank.priority}`} · {tank.direction ?? "Unplaced"} · {snapshot.sensors.filter((sensor) => sensor.tank_id === tank.id).length} sensors · {snapshot.gates.filter((gate) => gate.tank_id === tank.id).length} gates</small></div>
              <button className="text-button" onClick={() => { setEditingTank(tank.id); setTankName(tank.name); setTankPriority(tank.priority === null ? "" : String(tank.priority)); setTankDirection(tank.direction ?? ""); setTankParent(tank.parent_tank_id ?? ""); }}>Edit</button>
              <button className="tool-button" title={`Delete ${tank.name}`} aria-label={`Delete ${tank.name}`} disabled={busy}
                onClick={() => void update({
                  tanks: snapshot.tanks.filter((item) => item.id !== tank.id).map((item) => item.parent_tank_id === tank.id ? { ...item, parent_tank_id: null } : item),
                  gates: snapshot.gates.map((gate) => ({ ...gate,
                    tank_id: gate.tank_id === tank.id ? null : gate.tank_id,
                    destination_tank_id: gate.destination_tank_id === tank.id ? null : gate.destination_tank_id
                  })),
                  sensors: snapshot.sensors.map((sensor) => sensor.tank_id === tank.id ? { ...sensor, tank_id: null } : sensor)
                })}><Trash2 size={17} /></button>
            </div>
          ))}
          {!snapshot.tanks.length && <p className="empty-inline">No tanks configured.</p>}
        </div>
        <form className="inline-form" onSubmit={(event) => void addTank(event)}>
          <input aria-label="Tank name" placeholder="Tank name" value={tankName} onChange={(event) => setTankName(event.target.value)} required />
          <input className="priority-input" aria-label="Tank priority" title="Optional destination priority (0 is highest)" placeholder="Priority" type="number" min="0" step="1" value={tankPriority} onChange={(event) => setTankPriority(event.target.value)} />
          <select aria-label="Relative to tank" value={tankParent} onChange={(event) => setTankParent(event.target.value)}><option value="">Main</option>{snapshot.tanks.filter((tank) => tank.id !== editingTank).map((tank) => <option key={tank.id} value={tank.id}>{tank.name}</option>)}</select>
          <select aria-label="Tank direction" value={tankDirection} required={!!tankParent} onChange={(event) => setTankDirection(event.target.value as TankDirection | "")}><option value="">Direction unset</option><option value="N">North</option><option value="E">East</option><option value="S">South</option><option value="W">West</option></select>
          <button className="command-button" disabled={busy}><Plus size={17} /> {editingTank ? "Save tank" : "Add tank"}</button>
          {editingTank && <button type="button" className="text-button" onClick={() => { setEditingTank(null); setTankName(""); setTankPriority(""); setTankDirection(""); setTankParent(""); }}>Cancel</button>}
        </form>
      </section>

      <section className="config-section">
        <h2>Gates</h2>
        <div className="item-list">
          {snapshot.gates.map((gate) => (
            <div className="config-item" key={gate.id}>
              <div><div className="gate-heading"><strong>{gate.name}</strong>{!gate.lever_name && <span className="gate-warning" title="Missing lever"><AlertTriangle size={15} aria-hidden="true" /> Missing lever</span>}</div><small>{gate.type} · Opens when lever {gate.open_when_on ? "on" : "off"} · {snapshot.tanks.find((tank) => tank.id === gate.tank_id)?.name ?? "No source"} → {snapshot.tanks.find((tank) => tank.id === gate.destination_tank_id)?.name ?? "Outlet"} · {gate.lever_name ?? "No lever"}</small>
              <small>{snapshot.connection.ok && gate.lever_name && snapshot.levers.some((lever) => lever.name === gate.lever_name)
                ? `Lever ${snapshot.levers.find((lever) => lever.name === gate.lever_name)?.state ? "on" : "off"}; gate ${snapshot.levers.find((lever) => lever.name === gate.lever_name)?.state === gate.open_when_on ? "open" : "closed"} (configured)`
                : "Lever state unavailable"}</small></div>
              <button className="text-button" onClick={() => {
                setEditingGate(gate.id); setGateName(gate.name); setGateType(gate.type);
                setGateTank(gate.tank_id ?? ""); setGateDestination(gate.destination_tank_id ?? "");
                setGateLever(gate.lever_name ?? ""); setOpenWhenOn(gate.open_when_on);
              }}>Edit</button>
              <button className="tool-button" title={`Delete ${gate.name}`} aria-label={`Delete ${gate.name}`} disabled={busy}
                onClick={() => void update({ gates: snapshot.gates.filter((item) => item.id !== gate.id) })}><Trash2 size={17} /></button>
            </div>
          ))}
          {!snapshot.gates.length && <p className="empty-inline">No gates configured.</p>}
        </div>
        <form className="form-grid" onSubmit={(event) => void addGate(event)}>
          <label>Name<input value={gateName} onChange={(event) => setGateName(event.target.value)} required /></label>
          <label>Type<select value={gateType} onChange={(event) => setGateType(event.target.value as GateType)}>
            {gateTypes.map((type) => <option key={type}>{type}</option>)}
          </select></label>
          <label className="check-label"><input type="checkbox" checked={openWhenOn} onChange={(event) => setOpenWhenOn(event.target.checked)} /> Lever on opens gate</label>
          <label>Source tank<select value={gateTank} onChange={(event) => setGateTank(event.target.value)}><option value="">None</option>{snapshot.tanks.map((tank) => <option value={tank.id} key={tank.id}>{tank.name}</option>)}</select></label>
          <label>Destination tank<select value={gateDestination} onChange={(event) => setGateDestination(event.target.value)}><option value="">Outlet / none</option>{snapshot.tanks.map((tank) => <option value={tank.id} key={tank.id}>{tank.name}</option>)}</select></label>
          <label>HTTP Lever<select value={gateLever} onChange={(event) => setGateLever(event.target.value)}><option value="">None</option>{snapshot.levers.map((lever) => <option value={lever.name} key={lever.name}>{lever.name}</option>)}</select></label>
          <button className="command-button" disabled={busy}><Plus size={17} /> {editingGate ? "Save gate" : "Add gate"}</button>
          {editingGate && <button type="button" className="text-button" onClick={() => { setEditingGate(null); setGateName(""); }}>Cancel</button>}
        </form>
      </section>

      <section className="config-section">
        <h2>Sensors</h2>
        {snapshot.sensors.length ? <div className="item-list">{snapshot.sensors.map((sensor) => {
          return <div className="sensor-row" key={sensor.adapter_name}>
            <div><strong>{sensor.adapter_name}</strong><small>Last known: {sensor.last_state === null ? "Unknown" : sensor.last_state ? "On" : "Off"}</small>
              {sensor.error && <small className="sensor-error">{sensor.error}</small>}
              <small>Last seen {sensor.last_seen_at ? new Date(sensor.last_seen_at).toLocaleString() : "never"}</small></div>
            <select aria-label={`Type for ${sensor.adapter_name}`} value={sensor.type ?? ""} disabled={busy}
              onChange={(event) => void update({ sensors: snapshot.sensors.map((item) => item.adapter_name === sensor.adapter_name
                ? { ...item, type: (event.target.value || null) as SensorType | null, role: null } : item) })}>
              <option value="">Unclassified</option>{sensorTypes.map((type) => <option key={type}>{type}</option>)}
            </select>
            <select aria-label={`Role for ${sensor.adapter_name}`} value={sensor.role ?? ""} disabled={busy || !sensor.type}
              onChange={(event) => void update({ sensors: snapshot.sensors.map((item) => item.adapter_name === sensor.adapter_name
                ? { ...item, role: (event.target.value || null) as SensorRole | null } : item) })}>
              <option value="">No role</option>{sensor.type && sensorRoles[sensor.type].map((role) => <option key={role} value={role}>{role.replace(/_/g, " ")}</option>)}
            </select>
            <select aria-label={`Tank for ${sensor.adapter_name}`} value={sensor.tank_id ?? ""} disabled={busy}
              onChange={(event) => void update({ sensors: snapshot.sensors.map((item) => item.adapter_name === sensor.adapter_name ? { ...item, tank_id: event.target.value || null } : item) })}>
              <option value="">No tank</option>{snapshot.tanks.map((tank) => <option value={tank.id} key={tank.id}>{tank.name}</option>)}</select>
          </div>;
        })}</div> : <p className="empty-inline">No sensors discovered yet.</p>}
      </section>

      <section className="config-section">
        <h2>Rules</h2>
        <div className="item-list">{snapshot.rules.map((item) => <div className="config-item" key={item.id}>
          <div><strong>{item.id}</strong><small>{item.enabled ? "Enabled" : "Disabled"}{item.description ? ` · ${item.description}` : ""}</small>
            <small>When {Object.entries(item.when_adapters).map(([name, expected]) => expected ? `${name} active` : `NOT ${name} active`).join(item.operator === "all" ? " AND " : " OR ") || "no sensor selected"} then {item.lever || "no lever selected"} {item.action === "switch_on" ? "active" : "inactive"}</small></div>
          <div className="item-actions"><button className="text-button" onClick={() => { setEditingRule(item.id); setRule(item); }}>Edit</button>
          <button className="tool-button" title={`Delete ${item.id}`} aria-label={`Delete ${item.id}`} disabled={busy}
            onClick={() => void update({ rules: snapshot.rules.filter((ruleItem) => ruleItem.id !== item.id) })}><Trash2 size={17} /></button></div>
        </div>)}</div>
        <form className="rule-form" onSubmit={(event) => void saveRule(event)}>
          <div className="form-grid">
            <label>Rule ID<input value={rule.id} onChange={(event) => setRule({ ...rule, id: event.target.value })} required /></label>
            <label>Description<input value={rule.description} onChange={(event) => setRule({ ...rule, description: event.target.value })} /></label>
            <label className="check-label"><input type="checkbox" checked={rule.enabled} onChange={(event) => setRule({ ...rule, enabled: event.target.checked })} /> Enabled</label>
          </div>
          <div className="rule-statement">
            <span className="rule-keyword">When</span>
            <div className="conditions">
              {ruleConditions.length === 0 && <div className="condition-row">
                <select className="sensor-select" aria-label="Sensor" value="" onChange={(event) => changeCondition(event.target.value, true)} required>
                  <option value="">Select sensor</option>{availableConditionNames.map((name) => <option value={name} key={name}>{name}</option>)}
                </select>
                <select aria-label="Sensor state" value="true" disabled><option value="true">Active</option></select>
              </div>}
              {ruleConditions.map(([name, expected], index) => <div className="condition-row" key={name}>
                {index > 0 && <select className="join-select" aria-label="Join conditions" value={rule.operator} onChange={(event) => setRule({ ...rule, operator: event.target.value as Rule["operator"] })}>
                  <option value="all">AND</option><option value="any">OR</option>
                </select>}
                <select className="sensor-select" aria-label={`Sensor ${index + 1}`} value={name} onChange={(event) => changeCondition(event.target.value, expected, name)}>
                  {availableConditionNames.map((candidate) => <option value={candidate} key={candidate} disabled={candidate !== name && candidate in rule.when_adapters}>{candidate}</option>)}
                </select>
                <select aria-label={`State for ${name}`} title="Inactive means NOT active" value={String(expected)} onChange={(event) => changeCondition(name, event.target.value === "true", name)}><option value="true">Active</option><option value="false">Inactive</option></select>
                <button type="button" className="tool-button" title={`Remove ${name}`} aria-label={`Remove ${name}`} onClick={() => changeCondition("", expected, name)}><Trash2 size={16} /></button>
              </div>)}
              <button type="button" className="text-button" disabled={!snapshot.sensors.some((sensor) => !(sensor.adapter_name in rule.when_adapters))} onClick={() => {
                const next = snapshot.sensors.find((sensor) => !(sensor.adapter_name in rule.when_adapters));
                if (next) changeCondition(next.adapter_name, true);
              }}><Plus size={16} /> Add sensor</button>
            </div>
            <span className="rule-keyword">Then</span>
            <div className="rule-output">
              <select aria-label="HTTP Lever ID" value={rule.lever} onChange={(event) => setRule({ ...rule, lever: event.target.value })} required>
                <option value="">Select HTTP Lever</option>{availableLeverNames.map((name) => <option value={name} key={name}>{name}</option>)}
              </select>
              <select aria-label="Lever state" value={rule.action} onChange={(event) => setRule({ ...rule, action: event.target.value as Rule["action"] })}>
                <option value="switch_on">Active</option><option value="switch_off">Inactive</option>
              </select>
            </div>
          </div>
          <div className="form-actions"><button className="command-button" disabled={busy || !ruleConditions.length || !rule.lever}><Save size={17} /> {editingRule ? "Save rule" : "Add rule"}</button>
          {editingRule && <button type="button" className="text-button" onClick={() => { setEditingRule(null); setRule(emptyRule()); }}>Cancel</button>}</div>
        </form>
      </section>
    </div>
  );
}

function emptyRule(): Rule {
  return { id: "", enabled: true, description: "", when_adapters: {}, operator: "all", action: "switch_on", lever: "" };
}
