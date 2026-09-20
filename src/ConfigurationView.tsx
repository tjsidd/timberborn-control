import { AlertTriangle, ArrowDown, ArrowUp, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import type { Configuration, Gate, GateType, Rule, RuleCondition, SensorRole, SensorType, Snapshot, TankDirection } from "./types";

type ConditionGroup = Extract<RuleCondition, { kind: "group" }>;

type Props = { snapshot: Snapshot; onSave: (config: Configuration) => Promise<boolean>; busy: boolean };

const sensorTypes: SensorType[] = ["Depth", "Resource", "Flow", "BadWater"];
const gateTypes: GateType[] = ["1m", "2m", "3m"];
const configTabs = ["Tanks", "Gates", "Sensors", "Rules", "Adaptive Flow Control"] as const;
type ConfigTab = typeof configTabs[number];
const sensorRoles: Record<SensorType, SensorRole[]> = {
  Depth: ["needs_water", "full", "reserve"], Resource: [], Flow: ["heavy_flow"], BadWater: ["badwater"]
};
const configOf = (snapshot: Snapshot): Configuration => ({
  auto_delete_missing_sensors: snapshot.auto_delete_missing_sensors,
  graph_positions: snapshot.graph_positions,
  adaptive_flow_enabled: snapshot.adaptive_flow_enabled,
  adaptive_target_percent: snapshot.adaptive_target_percent,
  adaptive_flow_estimates: snapshot.adaptive_flow_estimates,
  tanks: snapshot.tanks,
  gates: snapshot.gates,
  sensors: snapshot.sensors.map(({ adapter_name, type, role, tank_id, last_state, last_seen_at }) => ({ adapter_name, type, role, tank_id, last_state, last_seen_at })),
  rules: snapshot.rules
});

export function ConfigurationView({ snapshot, onSave, busy }: Props) {
  const [activeTab, setActiveTab] = useState<ConfigTab>("Tanks");
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
  const [adaptiveTargetDraft, setAdaptiveTargetDraft] = useState(String(snapshot.adaptive_target_percent));

  useEffect(() => {
    setAdaptiveTargetDraft(String(snapshot.adaptive_target_percent));
  }, [snapshot.adaptive_target_percent]);

  useEffect(() => {
    if (editingRule) {
      const current = snapshot.rules.find((item) => item.id === editingRule);
      if (current) setRule({ ...current, condition: conditionTree(current), when_adapters: {} });
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
    const condition = conditionTree(rule);
    if (!rule.id.trim() || !validCondition(condition)) return;
    const saved = { ...rule, id: rule.id.trim(), lever: rule.lever.trim(), condition, when_adapters: {} };
    const next = editingRule
      ? snapshot.rules.map((item) => item.id === editingRule ? saved : item)
      : [...snapshot.rules, saved];
    if (await update({ rules: next })) {
      setEditingRule(null);
      setRule(emptyRule());
    }
  }

  async function saveAdaptiveTarget(event: React.FormEvent) {
    event.preventDefault();
    await update({ adaptive_target_percent: Number(adaptiveTargetDraft) });
  }

  function changeNode(path: number[], change: (node: RuleCondition) => RuleCondition) {
    setRule((current) => ({ ...current, condition: mapCondition(conditionTree(current), path, change) }));
  }

  function removeNode(path: number[]) {
    const parent = path.slice(0, -1);
    const index = path[path.length - 1];
    changeNode(parent, (node) => removeConditionChild(node as ConditionGroup, index));
  }

  function ungroupNode(path: number[]) {
    const parent = path.slice(0, -1);
    const index = path[path.length - 1];
    changeNode(parent, (node) => {
      const group = node as ConditionGroup;
      const children = [...group.children];
      const nested = children[index] as ConditionGroup;
      if (!nested.children.length) return removeConditionChild(group, index);
      children.splice(index, 1, ...nested.children);
      const joins = groupJoins(group);
      joins.splice(index, 0, ...groupJoins(nested));
      return { ...group, children, joins };
    });
  }

  function moveNode(path: number[], index: number, direction: -1 | 1) {
    changeNode(path, (node) => {
      const group = node as ConditionGroup;
      const children = [...group.children];
      [children[index], children[index + direction]] = [children[index + direction], children[index]];
      return { ...group, children };
    });
  }

  function moveRule(index: number, direction: -1 | 1) {
    const rules = [...snapshot.rules];
    [rules[index], rules[index + direction]] = [rules[index + direction], rules[index]];
    void update({ rules });
  }

  function addNode(path: number[], kind: "sensor" | "group") {
    const name = snapshot.sensors.find((sensor) => !usedConditionNames.includes(sensor.adapter_name))?.adapter_name;
    if (!name) return;
    const sensor: RuleCondition = { kind: "sensor", adapter_name: name, active: true };
    const child: RuleCondition = kind === "sensor" ? sensor : { kind: "group", operator: "all", children: [sensor] };
    changeNode(path, (node) => {
      const group = node as ConditionGroup;
      return { ...group, children: [...group.children, child],
        joins: group.children.length ? [...groupJoins(group), "all" as const] : [] };
    });
  }

  function groupWithNext(path: number[], index: number) {
    changeNode(path, (node) => {
      const group = node as ConditionGroup;
      const children = [...group.children];
      const joins = groupJoins(group);
      const join = joins[index];
      children.splice(index, 2, { kind: "group", operator: join, joins: [join], children: children.slice(index, index + 2) });
      joins.splice(index, 1);
      return { ...group, children, joins };
    });
  }

  function changeJoin(path: number[], index: number, operator: ConditionGroup["operator"]) {
    changeNode(path, (node) => {
      const group = node as ConditionGroup;
      const joins = groupJoins(group);
      joins[index] = operator;
      return { ...group, joins };
    });
  }

  const condition = conditionTree(rule);
  const usedConditionNames = conditionNames(condition);
  const availableConditionNames = [...new Set([...snapshot.sensors.map((sensor) => sensor.adapter_name), ...usedConditionNames])];
  const availableLeverNames = [...new Set([...snapshot.levers.map((lever) => lever.name), ...(rule.lever ? [rule.lever] : [])])];

  function renderGroup(group: ConditionGroup, path: number[], siblingCount = 1): React.ReactNode {
    return <div className={`condition-group ${path.length ? "nested-condition-group" : ""}`} key={path.join("-") || "root"}>
      {path.length > 0 && <div className="condition-group-heading">
          <span className="group-paren">(</span>
          <button type="button" className="tool-button order-button" title="Move group up" aria-label={`Move group ${path.join(".")} up`} disabled={path[path.length - 1] === 0} onClick={() => moveNode(path.slice(0, -1), path[path.length - 1], -1)}><ArrowUp size={16} /></button>
          <button type="button" className="tool-button order-button" title="Move group down" aria-label={`Move group ${path.join(".")} down`} disabled={path[path.length - 1] === siblingCount - 1} onClick={() => moveNode(path.slice(0, -1), path[path.length - 1], 1)}><ArrowDown size={16} /></button>
          <button type="button" className="text-button" onClick={() => ungroupNode(path)}>Ungroup</button>
          <button type="button" className="tool-button" title="Remove group" aria-label="Remove group" onClick={() => removeNode(path)}><Trash2 size={16} /></button>
      </div>}
      <div className="condition-group-children">{group.children.map((child, index) => {
        const childPath = [...path, index];
        return <div className="condition-entry" key={childPath.join("-")}>
        {index > 0 && <select className="condition-join" aria-label={`Join ${path.join(".") || "root"} ${index}`} value={groupJoins(group)[index - 1]}
          onChange={(event) => changeJoin(path, index - 1, event.target.value as ConditionGroup["operator"])}>
          <option value="all">AND</option><option value="any">OR</option>
        </select>}
        {child.kind === "group" ? renderGroup(child, childPath, group.children.length) : <div className="condition-row">
          <select className="sensor-select" aria-label={`Sensor ${childPath.join(".")}`} value={child.adapter_name}
            onChange={(event) => changeNode(childPath, () => ({ ...child, adapter_name: event.target.value }))}>
            {availableConditionNames.map((name) => <option key={name} value={name} disabled={name !== child.adapter_name && usedConditionNames.includes(name)}>{name}</option>)}
          </select>
          <select aria-label={`State for ${child.adapter_name}`} value={String(child.active)}
            onChange={(event) => changeNode(childPath, () => ({ ...child, active: event.target.value === "true" }))}>
            <option value="true">Active</option><option value="false">Inactive</option>
          </select>
          <button type="button" className="tool-button order-button" title="Move condition up" aria-label={`Move ${child.adapter_name} up`} disabled={index === 0} onClick={() => moveNode(path, index, -1)}><ArrowUp size={16} /></button>
          <button type="button" className="tool-button order-button" title="Move condition down" aria-label={`Move ${child.adapter_name} down`} disabled={index === group.children.length - 1} onClick={() => moveNode(path, index, 1)}><ArrowDown size={16} /></button>
          <button type="button" className="tool-button" title={`Remove ${child.adapter_name}`} aria-label={`Remove ${child.adapter_name}`} onClick={() => removeNode(childPath)}><Trash2 size={16} /></button>
          {index < group.children.length - 1 && <button type="button" className="text-button group-next-button" onClick={() => groupWithNext(path, index)}>Group with next</button>}
        </div>}
        </div>;
      })}</div>
      <div className="condition-group-actions">
        <button type="button" className="text-button" disabled={!snapshot.sensors.some((sensor) => !usedConditionNames.includes(sensor.adapter_name))} onClick={() => addNode(path, "sensor")}><Plus size={16} /> Add sensor</button>
        <button type="button" className="text-button" disabled={!snapshot.sensors.some((sensor) => !usedConditionNames.includes(sensor.adapter_name))} onClick={() => addNode(path, "group")}><Plus size={16} /> Add group</button>
      </div>
      {path.length > 0 && <span className="group-paren">)</span>}
    </div>;
  }

  return (
    <div className="config-layout">
      <nav className="view-tabs config-tabs" aria-label="Configuration sections" role="tablist"
        onKeyDown={(event) => {
          const index = configTabs.indexOf(activeTab);
          const next = event.key === "ArrowRight" ? (index + 1) % configTabs.length
            : event.key === "ArrowLeft" ? (index - 1 + configTabs.length) % configTabs.length
              : event.key === "Home" ? 0 : event.key === "End" ? configTabs.length - 1 : -1;
          if (next < 0) return;
          event.preventDefault();
          setActiveTab(configTabs[next]);
          event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
        }}>
        {configTabs.map((tab) => <button key={tab} type="button" role="tab"
          id={`config-tab-${tab.toLowerCase().replace(/ /g, "-")}`}
          aria-controls={`config-panel-${tab.toLowerCase().replace(/ /g, "-")}`}
          aria-selected={activeTab === tab} tabIndex={activeTab === tab ? 0 : -1}
          className={activeTab === tab ? "selected" : ""}
          onClick={() => setActiveTab(tab)}>{tab}</button>)}
      </nav>
      {activeTab === "Tanks" && <section className="config-section" role="tabpanel" id="config-panel-tanks" aria-labelledby="config-tab-tanks">
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
        <form className="inline-form tank-form" onSubmit={(event) => void addTank(event)}>
          <label>Tank name<input value={tankName} onChange={(event) => setTankName(event.target.value)} required /></label>
          <label className="tank-priority">Priority (optional)<input title="Optional destination priority (0 is highest)" type="number" min="0" step="1" value={tankPriority} onChange={(event) => setTankPriority(event.target.value)} /></label>
          <label>Relative to tank<select value={tankParent} onChange={(event) => setTankParent(event.target.value)}><option value="">Main</option>{snapshot.tanks.filter((tank) => tank.id !== editingTank).map((tank) => <option key={tank.id} value={tank.id}>{tank.name}</option>)}</select></label>
          <label>Direction from tank<select value={tankDirection} required={!!tankParent} onChange={(event) => setTankDirection(event.target.value as TankDirection | "")}><option value="">Direction unset</option><option value="N">North</option><option value="NE">Northeast</option><option value="E">East</option><option value="SE">Southeast</option><option value="S">South</option><option value="SW">Southwest</option><option value="W">West</option><option value="NW">Northwest</option></select></label>
          <button className="command-button" disabled={busy}><Plus size={17} /> {editingTank ? "Save tank" : "Add tank"}</button>
          {editingTank && <button type="button" className="text-button" onClick={() => { setEditingTank(null); setTankName(""); setTankPriority(""); setTankDirection(""); setTankParent(""); }}>Cancel</button>}
        </form>
      </section>}

      {activeTab === "Gates" && <section className="config-section" role="tabpanel" id="config-panel-gates" aria-labelledby="config-tab-gates">
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
      </section>}

      {activeTab === "Sensors" && <section className="config-section" role="tabpanel" id="config-panel-sensors" aria-labelledby="config-tab-sensors">
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
      </section>}

      {activeTab === "Adaptive Flow Control" && <section className="config-section" role="tabpanel" id="config-panel-adaptive-flow-control" aria-labelledby="config-tab-adaptive-flow-control">
        <h2>Adaptive flow control <small className="beta-label">Beta</small></h2>
        <label className="check-label adaptive-toggle"><input type="checkbox" checked={snapshot.adaptive_flow_enabled} disabled={busy}
          onChange={(event) => void update({ adaptive_flow_enabled: event.target.checked })} /> Enabled</label>
        <form className="adaptive-target-form" onSubmit={(event) => void saveAdaptiveTarget(event)}>
          <label>Shutoff target (%)<input type="number" min="85.1" max="100" step="0.1" required value={adaptiveTargetDraft}
            onChange={(event) => setAdaptiveTargetDraft(event.target.value)} /></label>
          <button className="command-button" disabled={busy || Number(adaptiveTargetDraft) === snapshot.adaptive_target_percent}><Save size={16} /> Save target</button>
        </form>
        <div className="item-list adaptive-status-list">{snapshot.adaptive_flow_statuses.map((status) => <div className="config-item" key={status.gate_id}>
          <div><strong>{status.gate_name}</strong><small>{status.tank_name} · {status.eligibility_error ?? (status.fill_percent_per_second === null ? "Learning" : `${status.fill_percent_per_second.toFixed(3)}% / second`)}</small>
            {status.held_off && <small>Held closed until D65 clears</small>}
            {status.predicted_close_at && <small>Predicted cutoff {new Date(status.predicted_close_at).toLocaleTimeString()}</small>}
          </div>
        </div>)}
        {!snapshot.adaptive_flow_statuses.length && <p className="empty-inline">No configured input gates.</p>}</div>
      </section>}

      {activeTab === "Rules" && <section className="config-section" role="tabpanel" id="config-panel-rules" aria-labelledby="config-tab-rules">
        <h2>Rules</h2>
        <div className="item-list">{snapshot.rules.map((item, index) => <div className="config-item" key={item.id}>
          <div><strong>{item.id}</strong><small>{item.enabled ? "Enabled" : "Disabled"}{item.description ? ` · ${item.description}` : ""}</small>
            <small>When {formatCondition(conditionTree(item)) || "no sensor selected"} then {item.lever || "no lever selected"} {item.action === "switch_on" ? "active" : "inactive"}</small></div>
          <div className="item-actions">
          <button className="tool-button order-button" title={`Move ${item.id} up`} aria-label={`Move ${item.id} up`} disabled={busy || index === 0} onClick={() => moveRule(index, -1)}><ArrowUp size={16} /></button>
          <button className="tool-button order-button" title={`Move ${item.id} down`} aria-label={`Move ${item.id} down`} disabled={busy || index === snapshot.rules.length - 1} onClick={() => moveRule(index, 1)}><ArrowDown size={16} /></button>
          <button className="text-button" onClick={() => { setEditingRule(item.id); setRule({ ...item, condition: conditionTree(item), when_adapters: {} }); }}>Edit</button>
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
              {renderGroup(condition, [])}
              {validCondition(condition) && <small className="condition-preview">{formatCondition(condition)}</small>}
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
          <div className="form-actions"><button className="command-button" disabled={busy || !validCondition(condition) || !rule.lever}><Save size={17} /> {editingRule ? "Save rule" : "Add rule"}</button>
          {editingRule && <button type="button" className="text-button" onClick={() => { setEditingRule(null); setRule(emptyRule()); }}>Cancel</button>}</div>
        </form>
      </section>}
    </div>
  );
}

function emptyRule(): Rule {
  return { id: "", enabled: true, description: "", when_adapters: {}, operator: "all", condition: { kind: "group", operator: "all", children: [] }, action: "switch_on", lever: "" };
}

function conditionTree(rule: Rule): ConditionGroup {
  if (rule.condition?.kind === "group") return rule.condition;
  if (rule.condition?.kind === "sensor") return { kind: "group", operator: "all", children: [rule.condition] };
  return { kind: "group", operator: rule.operator, children: Object.entries(rule.when_adapters).map(([adapter_name, active]) => ({ kind: "sensor", adapter_name, active })) };
}

function conditionNames(condition: RuleCondition): string[] {
  return condition.kind === "sensor" ? [condition.adapter_name] : condition.children.flatMap(conditionNames);
}

function groupJoins(group: ConditionGroup): ConditionGroup["operator"][] {
  return group.joins ? [...group.joins] : Array(Math.max(0, group.children.length - 1)).fill(group.operator);
}

function removeConditionChild(group: ConditionGroup, index: number): ConditionGroup {
  const children = [...group.children];
  const joins = groupJoins(group);
  children.splice(index, 1);
  if (joins.length) joins.splice(index === 0 ? 0 : index - 1, 1);
  return { ...group, children, joins };
}

function validCondition(condition: RuleCondition): boolean {
  return condition.kind === "sensor" ? !!condition.adapter_name
    : condition.children.length > 0 && groupJoins(condition).length === condition.children.length - 1 && condition.children.every(validCondition);
}

function mapCondition(group: ConditionGroup, path: number[], change: (node: RuleCondition) => RuleCondition): ConditionGroup {
  if (!path.length) return change(group) as ConditionGroup;
  const [index, ...rest] = path;
  return { ...group, children: group.children.map((child, childIndex) => childIndex !== index ? child
    : rest.length ? mapCondition(child as ConditionGroup, rest, change) : change(child)) };
}

function formatCondition(condition: RuleCondition, nested = false): string {
  if (condition.kind === "sensor") return `${condition.adapter_name} ${condition.active ? "active" : "inactive"}`;
  const joins = groupJoins(condition);
  let text = condition.children.length ? formatCondition(condition.children[0], true) : "";
  for (let index = 1; index < condition.children.length; index += 1) {
    if (index > 1 && joins[index - 1] !== joins[index - 2]) text = `(${text})`;
    text += ` ${joins[index - 1] === "all" ? "AND" : "OR"} ${formatCondition(condition.children[index], true)}`;
  }
  return nested && text ? `(${text})` : text;
}
