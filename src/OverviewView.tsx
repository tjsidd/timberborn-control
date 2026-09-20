import { Background, Controls, Handle, MiniMap, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";
import { AlertTriangle, ArrowDownUp, Crosshair, Droplets, LogIn, LogOut, Maximize2, RotateCcw, X } from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";

import type { Configuration, Gate, GraphPosition, Sensor, Snapshot, Tank, TankDirection } from "./types";

type Props = { snapshot: Snapshot; onSave: (config: Configuration) => Promise<boolean>; busy: boolean; error?: string | null };
type Selection = { kind: "tank" | "gate" | "anchor"; id: string } | null;
type Level = { text: string; stale: boolean };
type TankNode = Node<{ tank: Tank; sensors: Sensor[]; gateCount: number; unplaced: boolean; main: boolean; level: Level }, "tank">;
type GateNode = Node<{ gate: Gate; state: "open" | "closed" | "unknown"; unresolved: boolean; assumed: boolean }, "gate">;
type AnchorNode = Node<{ label: string; kind: "input" | "output" | "assumed"; inletGateId?: string; outletGateId?: string; parentTankId?: string }, "anchor">;
type MapNode = TankNode | GateNode | AnchorNode;
type Direction = TankDirection;
type Relation = { parentId: string; direction: Direction };
type AssumedDraft = { anchorId: string; name: string; priority: string; direction: TankDirection; parentTankId: string; inletGateId: string; outletGateId: string; sensorNames: string[] };
const directions: Record<Direction, { x: number; y: number; label: string; position: Position }> = {
  N: { x: 0, y: -460, label: "North", position: Position.Top },
  NE: { x: 620, y: -460, label: "Northeast", position: Position.Top },
  E: { x: 620, y: 0, label: "East", position: Position.Right },
  SE: { x: 620, y: 460, label: "Southeast", position: Position.Bottom },
  S: { x: 0, y: 460, label: "South", position: Position.Bottom },
  SW: { x: -620, y: 460, label: "Southwest", position: Position.Bottom },
  W: { x: -620, y: 0, label: "West", position: Position.Left },
  NW: { x: -620, y: -460, label: "Northwest", position: Position.Top }
};
type Cardinal = "N" | "E" | "S" | "W";
const opposite: Record<Cardinal, Cardinal> = { N: "S", E: "W", S: "N", W: "E" };
const mapNodeTypes = { tank: TankNodeView, gate: GateNodeView, anchor: AnchorNodeView };
const elk = new ELK();
const tankWidth = 260;
const gateWidth = 156;
const gateHeight = 50;
const tankHeight = (count: number) => 105 + Math.max(1, count) * 25;

function Handles() {
  return <>{(["N", "E", "S", "W"] as Cardinal[]).map((direction) => <Fragment key={direction}>
    <Handle type="source" id={`${direction}-source`} position={directions[direction].position} className="map-handle" />
    <Handle type="target" id={`${direction}-target`} position={directions[direction].position} className="map-handle" />
  </Fragment>)}</>;
}

function waterLevel(sensors: Sensor[]): Level {
  const readings = sensors.flatMap((sensor) => {
    const match = sensor.adapter_name.match(/\bD(\d+)$/i);
    const state = sensor.state ?? sensor.last_state;
    return match && state !== null ? [{ threshold: Number(match[1]), state, stale: sensor.state === null }] : [];
  });
  if (!readings.length) return { text: "Unknown", stale: false };
  const lower = Math.max(0, ...readings.filter((reading) => reading.state).map((reading) => reading.threshold));
  const upper = Math.min(Infinity, ...readings.filter((reading) => !reading.state).map((reading) => reading.threshold));
  const stale = readings.some((reading) => reading.stale);
  if (lower >= upper) return { text: "Conflicting signals", stale };
  if (upper === Infinity) return { text: `>${lower}`, stale };
  if (lower === 0) return { text: `0–${upper}`, stale };
  return { text: `${lower}–${upper}`, stale };
}

function TankNodeView({ data }: NodeProps<TankNode>) {
  return <div className={`map-tank${data.main ? " main" : ""}${data.unplaced ? " unplaced" : ""}`}>
    <Handles />
    <div className="map-tank-icon"><Droplets size={19} aria-hidden="true" /></div>
    <div className="map-tank-text"><strong>{data.tank.name}</strong><span>{data.main ? "Center" : data.unplaced ? "Direction unset" : data.tank.priority === null ? "Tank" : `Priority ${data.tank.priority}`}</span><em>Level {data.level.text}{data.level.stale ? " · last known" : ""}</em></div>
    <div className="map-tank-sensors">{data.sensors.length ? data.sensors.map((sensor) => <div className="map-sensor" key={sensor.adapter_name} title={`${sensor.adapter_name}: ${sensor.error ?? (sensor.state ? "Active" : sensor.state === false ? "Inactive" : "Unknown")}`}>
      <span className={`sensor-dot ${sensor.error ? "missing" : sensor.state === true ? "active" : sensor.state === false ? "inactive" : "unknown"}`} />
      <span>{sensor.adapter_name.replace(/^HTTP /, "")}</span>
    </div>) : <span className="map-no-sensors">No sensors</span>}</div>
    <div className="map-tank-stats"><ArrowDownUp size={13} /> {data.gateCount} gates</div>
  </div>;
}

function GateNodeView({ data }: NodeProps<GateNode>) {
  return <div className={`map-gate ${data.state}${data.unresolved ? " unresolved" : ""}${data.assumed ? " assumed" : ""}`}>
    <Handles />
    <ArrowDownUp size={15} aria-hidden="true" />
    <span>{data.gate.name}</span>
    <strong>{data.state === "unknown" ? "?" : data.state === "open" ? "Open" : "Closed"}</strong>
  </div>;
}

function AnchorNodeView({ data }: NodeProps<AnchorNode>) {
  return <div className={`map-anchor ${data.kind}`}><Handles />
    {data.kind === "input" ? <LogIn size={17} /> : data.kind === "output" ? <LogOut size={17} /> : <AlertTriangle size={17} />}
    <span>{data.label}</span>
  </div>;
}

function relationFor(tank: Tank, tanks: Tank[], main: Tank): Relation | null {
  if (tank.id === main.id) return null;
  if (tank.direction) return { parentId: tank.parent_tank_id ?? main.id, direction: tank.direction };
  const name = tank.name.trim();
  if (/^(NE|NW|SE|SW|[NESW])$/i.test(name)) return { parentId: main.id, direction: name.toUpperCase() as Direction };
  const suffix = name.match(/^(.+)\s+(NE|NW|SE|SW|[NESW])$/i);
  if (!suffix) return null;
  const parent = tanks.find((candidate) => candidate.name.trim().toLowerCase() === suffix[1].trim().toLowerCase());
  return parent && parent.id !== tank.id ? { parentId: parent.id, direction: suffix[2].toUpperCase() as Direction } : null;
}

function buildGraph(snapshot: Snapshot, compact: boolean): { nodes: MapNode[]; edges: Edge[]; unplaced: Tank[]; assumptions: Record<string, string>; unresolvedGateIds: string[] } {
  const main = snapshot.tanks.find((tank) => tank.name.trim().toLowerCase() === "main");
  const positions = new Map<string, { x: number; y: number }>();
  const relations = new Map<string, Relation>();
  if (main) {
    positions.set(main.id, { x: 0, y: 0 });
    for (const tank of snapshot.tanks) {
      const relation = relationFor(tank, snapshot.tanks, main);
      if (relation) relations.set(tank.id, relation);
    }
    for (let pass = 0; pass < snapshot.tanks.length; pass++) {
      let changed = false;
      for (const tank of snapshot.tanks) {
        const relation = relations.get(tank.id);
        if (!relation || positions.has(tank.id)) continue;
        const parent = positions.get(relation.parentId);
        if (!parent) continue;
        const siblings = snapshot.tanks.filter((candidate) => {
          const candidateRelation = relations.get(candidate.id);
          return candidateRelation?.parentId === relation.parentId && candidateRelation.direction === relation.direction;
        });
        const index = siblings.findIndex((candidate) => candidate.id === tank.id);
        const offset = (index - (siblings.length - 1) / 2) * (relation.direction === "N" || relation.direction === "S" ? 330 : 360);
        const direction = directions[relation.direction];
        positions.set(tank.id, { x: parent.x + direction.x + (direction.x === 0 ? offset : 0), y: parent.y + direction.y + (direction.x === 0 ? 0 : offset) });
        changed = true;
      }
      if (!changed) break;
    }
  }
  const unplaced = snapshot.tanks.filter((tank) => !positions.has(tank.id));
  const unplacedX = Math.max(0, ...Array.from(positions.values(), (position) => position.x)) + 310;
  const displayPositions = new Map(positions);
  for (const tank of unplaced) {
    displayPositions.set(tank.id, compact
      ? { x: 0, y: (main ? 340 : 0) + unplaced.findIndex((item) => item.id === tank.id) * 190 }
      : { x: unplacedX, y: unplaced.findIndex((item) => item.id === tank.id) * 205 - 130 });
  }
  const nodes: MapNode[] = snapshot.tanks.map((tank) => ({
    id: tank.id, type: "tank", position: displayPositions.get(tank.id)!, width: tankWidth, height: tankHeight(snapshot.sensors.filter((sensor) => sensor.tank_id === tank.id).length),
    data: { tank, sensors: snapshot.sensors.filter((sensor) => sensor.tank_id === tank.id), gateCount: snapshot.gates.filter((gate) => gate.tank_id === tank.id || gate.destination_tank_id === tank.id).length,
      unplaced: !positions.has(tank.id), main: tank.id === main?.id, level: waterLevel(snapshot.sensors.filter((sensor) => sensor.tank_id === tank.id)) },
    draggable: true
  }));
  const edges: Edge[] = [];
  for (const tank of snapshot.tanks) {
    const relation = relations.get(tank.id);
    if (!relation || !positions.has(tank.id)) continue;
    const branchSide: Cardinal = relation.direction === "N" || relation.direction === "S" ? "W" : "N";
    const targetSide = relation.direction.length === 2 ? opposite[relation.direction.includes("E") ? "E" : "W"] : branchSide;
    edges.push({ id: `branch-${tank.id}`, source: relation.parentId, target: tank.id,
      sourceHandle: `${relation.direction.length === 2 ? opposite[targetSide] : branchSide}-source`, targetHandle: `${targetSide}-target`,
      type: "straight", className: "map-branch", selectable: false });
  }

  const assumptions: Record<string, string> = {};
  const overrides = new Map<string, { source?: string; destination?: string }>();
  for (const second of snapshot.gates) {
    const match = second.name.match(/^(.+) Output 2$/i);
    if (!match || second.destination_tank_id) continue;
    const first = snapshot.gates.find((candidate) => candidate.name.toLowerCase() === `${match[1]} Output`.toLowerCase());
    if (!first?.tank_id || first.destination_tank_id || !displayPositions.has(first.tank_id)) continue;
    const source = displayPositions.get(first.tank_id)!;
    const assumedId = `assumed-${first.id}`;
    const assumedPosition = { x: source.x + 40, y: source.y + 460 };
    nodes.push({ id: assumedId, type: "anchor", position: assumedPosition, width: 180, height: 66,
      data: { label: `Assumed tank · ${match[1]}`, kind: "assumed", inletGateId: first.id, outletGateId: second.id, parentTankId: first.tank_id }, draggable: true });
    displayPositions.set(assumedId, assumedPosition);
    overrides.set(first.id, { destination: assumedId });
    overrides.set(second.id, { source: assumedId, destination: "terminal-output" });
    const note = `Assumed unnamed tank between ${first.name} and ${second.name}. Saved gate endpoints were not changed.`;
    assumptions[first.id] = note;
    assumptions[second.id] = note;
  }

  const tankY = Array.from(displayPositions.values(), (position) => position.y);
  const inputPosition = { x: 40, y: Math.min(-460, ...tankY) - 470 };
  const outputPosition = { x: 40, y: Math.max(0, ...tankY) + 520 };
  nodes.push({ id: "terminal-input", type: "anchor", position: inputPosition, width: 180, height: 50, data: { label: "Input", kind: "input" }, draggable: true });
  nodes.push({ id: "terminal-output", type: "anchor", position: outputPosition, width: 180, height: 50, data: { label: "Output", kind: "output" }, draggable: true });
  displayPositions.set("terminal-input", inputPosition);
  displayPositions.set("terminal-output", outputPosition);
  const centerOf = (id: string) => {
    const position = displayPositions.get(id)!;
    return id.startsWith("terminal-") ? { x: position.x + 85, y: position.y + 22 }
      : id.startsWith("assumed-") ? { x: position.x + 90, y: position.y + 33 }
      : { x: position.x + tankWidth / 2, y: position.y + (nodes.find((node) => node.id === id)?.height ?? 130) / 2 };
  };
  const gateEndpoints = snapshot.gates.map((gate) => {
    const override = overrides.get(gate.id);
    const source = override?.source ?? gate.tank_id ?? (/\b(?:Input|In)$/i.test(gate.name) ? "terminal-input" : null);
    const destination = override?.destination ?? gate.destination_tank_id ?? (/\b(?:Output|Out)(?: \d+)?$/i.test(gate.name) ? "terminal-output" : null);
    return { gate, source, destination };
  });
  const pairCounts = new Map<string, number>();
  for (const { source, destination } of gateEndpoints) {
    if (source && destination) {
      const pair = `${source}|${destination}`;
      pairCounts.set(pair, (pairCounts.get(pair) ?? 0) + 1);
    }
  }
  const pairIndexes = new Map<string, number>();
  const unresolvedGateIds: string[] = [];
  const unknownX = Math.max(...Array.from(displayPositions.values(), (position) => position.x)) + 440;
  let unknownIndex = 0;
  const outletIndexes = new Map<string, number>();
  for (const { gate, source, destination } of gateEndpoints) {
    const lever = snapshot.levers.find((item) => item.name === gate.lever_name);
    const state = !lever || !snapshot.connection.ok ? "unknown" : lever.state === gate.open_when_on ? "open" : "closed";
    const resolved = !!source && !!destination && displayPositions.has(source) && displayPositions.has(destination);
    let gateCenter: { x: number; y: number };
    if (resolved) {
      const sourceCenter = centerOf(source!);
      const destinationCenter = centerOf(destination!);
      const pair = `${source}|${destination}`;
      const index = pairIndexes.get(pair) ?? 0;
      pairIndexes.set(pair, index + 1);
      const offset = (index - ((pairCounts.get(pair) ?? 1) - 1) / 2) * 190;
      if (destination === "terminal-output" && source !== "terminal-input") {
        const outletIndex = outletIndexes.get(source!) ?? 0;
        outletIndexes.set(source!, outletIndex + 1);
        gateCenter = { x: sourceCenter.x + 245 + outletIndex * 190, y: sourceCenter.y + 215 };
      } else {
        gateCenter = { x: (sourceCenter.x + destinationCenter.x) / 2 + (Math.abs(destinationCenter.x - sourceCenter.x) > Math.abs(destinationCenter.y - sourceCenter.y) ? 0 : offset),
          y: (sourceCenter.y + destinationCenter.y) / 2 + (Math.abs(destinationCenter.x - sourceCenter.x) > Math.abs(destinationCenter.y - sourceCenter.y) ? offset : 0) };
      }
    } else {
      gateCenter = { x: unknownX + 78, y: inputPosition.y + 180 + unknownIndex * 100 };
      unknownIndex++;
      unresolvedGateIds.push(gate.id);
    }
    const nodeId = `gate-${gate.id}`;
    nodes.push({ id: nodeId, type: "gate", position: { x: gateCenter.x - gateWidth / 2, y: gateCenter.y - gateHeight / 2 }, width: gateWidth, height: gateHeight,
      data: { gate, state, unresolved: !resolved, assumed: gate.id in assumptions }, draggable: true });
    if (source && displayPositions.has(source)) edges.push({ id: `${nodeId}-in`, source, target: nodeId,
      ...edgeHandles(centerOf(source), gateCenter), type: "straight", className: `map-gate-edge ${state}${resolved ? "" : " unresolved"}`, data: { gateId: gate.id } });
    if (destination && displayPositions.has(destination)) edges.push({ id: `${nodeId}-out`, source: nodeId, target: destination,
      ...edgeHandles(gateCenter, centerOf(destination)), type: "straight", className: `map-gate-edge ${state}${resolved ? "" : " unresolved"}`, data: { gateId: gate.id } });
  }
  return { nodes, edges, unplaced, assumptions, unresolvedGateIds };
}

function configurationOf(snapshot: Snapshot): Configuration {
  return { auto_delete_missing_sensors: snapshot.auto_delete_missing_sensors, graph_positions: snapshot.graph_positions,
    adaptive_flow_enabled: snapshot.adaptive_flow_enabled, adaptive_target_percent: snapshot.adaptive_target_percent,
    adaptive_flow_estimates: snapshot.adaptive_flow_estimates, tanks: snapshot.tanks,
    gates: snapshot.gates,
    sensors: snapshot.sensors.map(({ adapter_name, type, role, tank_id, last_state, last_seen_at }) => ({ adapter_name, type, role, tank_id, last_state, last_seen_at })),
    rules: snapshot.rules };
}

function gateState(gate: Gate, snapshot: Snapshot): string {
  const lever = snapshot.levers.find((item) => item.name === gate.lever_name);
  if (!lever || !snapshot.connection.ok) return "State unavailable";
  return lever.state === gate.open_when_on ? "Open" : "Closed";
}

function edgeHandles(source: { x: number; y: number }, target: { x: number; y: number }) {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const direction: Cardinal = Math.abs(dx) > Math.abs(dy) ? dx > 0 ? "E" : "W" : dy > 0 ? "S" : "N";
  return { sourceHandle: `${direction}-source`, targetHandle: `${opposite[direction]}-target` };
}

async function removeOverlaps(nodes: MapNode[]): Promise<Map<string, { x: number; y: number }>> {
  const result = await elk.layout({
    id: "water-network",
    layoutOptions: { "elk.algorithm": "sporeOverlap", "elk.spacing.nodeNode": "52" },
    children: nodes.map((node) => ({
      id: node.id, x: node.position.x, y: node.position.y,
      width: node.width ?? 180, height: node.height ?? 50
    }))
  });
  return new Map(result.children?.map((node) => [node.id, { x: node.x ?? 0, y: node.y ?? 0 }]) ?? []);
}

function placedGraph(graph: ReturnType<typeof buildGraph>, positions: Map<string, { x: number; y: number }>) {
  const nodes = graph.nodes.map((node) => ({ ...node, position: positions.get(node.id) ?? node.position }));
  const centers = new Map(nodes.map((node) => [node.id, {
    x: node.position.x + (node.width ?? 180) / 2,
    y: node.position.y + (node.height ?? 50) / 2
  }]));
  const edges = graph.edges.map((edge) => {
    if (edge.id.startsWith("branch-")) return edge;
    const source = centers.get(edge.source);
    const target = centers.get(edge.target);
    return source && target ? { ...edge, ...edgeHandles(source, target) } : edge;
  });
  return { ...graph, nodes, edges };
}

export function OverviewView({ snapshot, onSave, busy, error }: Props) {
  const [selection, setSelection] = useState<Selection>(null);
  const [assumedDraft, setAssumedDraft] = useState<AssumedDraft | null>(null);
  const [draftError, setDraftError] = useState("");
  const [layout, setLayout] = useState<{ key: string; positions: Map<string, { x: number; y: number }> } | null>(null);
  const [dragLayout, setDragLayout] = useState<{ sessionId: string; positions: Map<string, GraphPosition> }>({ sessionId: snapshot.active_session_id, positions: new Map() });
  const fitCore = useRef<(() => void) | null>(null);
  const fitAll = useRef<(() => void) | null>(null);
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 600px)").matches);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 600px)");
    const update = () => setCompact(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const baseGraph = useMemo(() => buildGraph(snapshot, compact), [snapshot, compact]);
  const layoutKey = JSON.stringify({ compact, tanks: snapshot.tanks, gates: snapshot.gates.map(({ id, name, tank_id, destination_tank_id }) => ({ id, name, tank_id, destination_tank_id })), sensors: snapshot.sensors.map(({ adapter_name, tank_id }) => ({ adapter_name, tank_id })) });
  useEffect(() => {
    let cancelled = false;
    void removeOverlaps(baseGraph.nodes).then((positions) => {
      if (!cancelled) setLayout({ key: layoutKey, positions });
    }).catch(() => {
      if (!cancelled) setLayout({ key: layoutKey, positions: new Map() });
    });
    return () => { cancelled = true; };
  }, [layoutKey]);
  const dragPositions = dragLayout.sessionId === snapshot.active_session_id ? dragLayout.positions : new Map<string, GraphPosition>();
  const positions = new Map<string, GraphPosition>(layout?.key === layoutKey ? layout.positions : []);
  for (const [id, position] of Object.entries(snapshot.graph_positions)) positions.set(id, position);
  for (const [id, position] of dragPositions) positions.set(id, position);
  const graph = placedGraph(baseGraph, positions);
  useEffect(() => {
    if (layout?.key === layoutKey) requestAnimationFrame(() => fitCore.current?.());
  }, [layout?.key, layoutKey]);
  const main = snapshot.tanks.find((tank) => tank.name.trim().toLowerCase() === "main");
  const tank = selection?.kind === "tank" ? snapshot.tanks.find((item) => item.id === selection.id) : !selection ? main : undefined;
  const gate = selection?.kind === "gate" ? snapshot.gates.find((item) => item.id === selection.id) : undefined;
  const anchor = selection?.kind === "anchor" ? graph.nodes.find((item) => item.id === selection.id && item.type === "anchor") as AnchorNode | undefined : undefined;
  const tankLevel = tank ? waterLevel(snapshot.sensors.filter((sensor) => sensor.tank_id === tank.id)) : null;
  const unassignedSensors = snapshot.sensors.filter((sensor) => !sensor.tank_id);
  const unknownGates = snapshot.gates.filter((item) => graph.unresolvedGateIds.includes(item.id));

  function moveNode(id: string, position: GraphPosition) {
    setDragLayout((current) => ({ sessionId: snapshot.active_session_id,
      positions: new Map(current.sessionId === snapshot.active_session_id ? current.positions : []).set(id, position) }));
  }

  async function saveNodePosition(id: string, position: GraphPosition) {
    const saved = await onSave({ ...configurationOf(snapshot), graph_positions: { ...snapshot.graph_positions, [id]: position } });
    if (!saved) setDragLayout((current) => {
      if (current.sessionId !== snapshot.active_session_id) return current;
      const next = new Map(current.positions);
      next.delete(id);
      return { ...current, positions: next };
    });
  }

  async function resetNodePositions() {
    if (await onSave({ ...configurationOf(snapshot), graph_positions: {} })) {
      setDragLayout({ sessionId: snapshot.active_session_id, positions: new Map() });
      requestAnimationFrame(() => fitCore.current?.());
    }
  }

  function editAssumed(node: AnchorNode) {
    setDraftError("");
    setAssumedDraft({ anchorId: node.id, name: "", priority: "", direction: "S",
      parentTankId: node.data.parentTankId ?? "", inletGateId: node.data.inletGateId ?? "",
      outletGateId: node.data.outletGateId ?? "", sensorNames: [] });
  }

  async function saveAssumed(event: React.FormEvent) {
    event.preventDefault();
    if (!assumedDraft) return;
    setDraftError("");
    const name = assumedDraft.name.trim();
    if (snapshot.tanks.some((item) => item.name.toLowerCase() === name.toLowerCase())) {
      setDraftError("A tank with this name already exists.");
      return;
    }
    if (assumedDraft.inletGateId === assumedDraft.outletGateId) {
      setDraftError("Choose different inlet and outlet gates.");
      return;
    }
    const id = crypto.randomUUID();
    const configuration = configurationOf(snapshot);
    const graphPositions = { ...configuration.graph_positions };
    graphPositions[id] = graph.nodes.find((node) => node.id === assumedDraft.anchorId)?.position ?? { x: 0, y: 0 };
    delete graphPositions[assumedDraft.anchorId];
    const saved = await onSave({ ...configuration,
      graph_positions: graphPositions,
      tanks: [...configuration.tanks, { id, name, priority: assumedDraft.priority === "" ? null : Number(assumedDraft.priority), direction: assumedDraft.direction, parent_tank_id: assumedDraft.parentTankId || null }],
      gates: configuration.gates.map((gate) => gate.id === assumedDraft.inletGateId ? { ...gate, destination_tank_id: id }
        : gate.id === assumedDraft.outletGateId ? { ...gate, tank_id: id } : gate),
      sensors: configuration.sensors.map((sensor) => assumedDraft.sensorNames.includes(sensor.adapter_name) ? { ...sensor, tank_id: id } : sensor)
    });
    if (saved) {
      setAssumedDraft(null);
      setSelection({ kind: "tank", id });
    }
  }

  async function setDirection(target: Tank, direction: TankDirection | null) {
    await onSave({ ...configurationOf(snapshot), tanks: snapshot.tanks.map((item) => item.id === target.id ? { ...item, direction } : item) });
  }

  async function assignSensor(sensor: Sensor, tankId: string | null) {
    await onSave({ ...configurationOf(snapshot), sensors: configurationOf(snapshot).sensors.map((item) => item.adapter_name === sensor.adapter_name ? { ...item, tank_id: tankId } : item) });
  }

  return <div className="overview-layout">
    <section className="network-section">
      <div className="network-heading"><h2>Water network</h2><div className="network-key"><span className="key-line open" /> Open gate <span className="key-line closed" /> Closed gate <span className="key-dot" /> Unplaced / unknown</div><div className="network-actions"><button className="map-tool-button" title="Focus Main" aria-label="Focus Main" onClick={() => fitCore.current?.()}><Crosshair size={17} /></button><button className="map-tool-button" title="Fit entire network" aria-label="Fit entire network" onClick={() => fitAll.current?.()}><Maximize2 size={17} /></button><button className="map-tool-button" title="Reset node positions" aria-label="Reset node positions" disabled={busy || (!Object.keys(snapshot.graph_positions).length && !dragPositions.size)} onClick={() => void resetNodePositions()}><RotateCcw size={17} /></button></div></div>
      {snapshot.tanks.length ? <div className="network-canvas" aria-label="Tank and gate network">
        <ReactFlow key={compact ? "compact" : "wide"} nodes={graph.nodes} edges={graph.edges} nodeTypes={mapNodeTypes} minZoom={0.2} maxZoom={1.8}
          nodesDraggable={!busy} nodesConnectable={false} zoomOnDoubleClick={false} proOptions={{ hideAttribution: true }}
          onInit={(instance) => {
            fitCore.current = () => {
              const mainNode = instance.getNodes().find((node) => node.type === "tank" && node.data.main);
              const focus = instance.getNodes().filter((node) => node.type === "tank" && !node.data.unplaced &&
                (!mainNode || node.id === mainNode.id || (compact
                  ? Math.abs(node.position.x - mainNode.position.x) < 200 && node.position.y < mainNode.position.y
                  : node.position.x <= mainNode.position.x && node.position.y <= mainNode.position.y)));
              void instance.fitView({ nodes: focus.length ? focus : instance.getNodes().filter((node) => node.type === "tank"), padding: compact ? 0.08 : 0.18, maxZoom: compact ? 0.9 : 1.05, duration: 250 });
            };
            fitAll.current = () => { void instance.fitView({ padding: 0.1, maxZoom: 1.05, duration: 250 }); };
            fitCore.current();
          }}
          onNodeClick={(_, node) => {
            if (node.type === "anchor" && node.data.kind === "assumed") editAssumed(node as AnchorNode);
            setSelection(node.type === "gate" ? { kind: "gate", id: node.data.gate.id } : { kind: node.type === "anchor" ? "anchor" : "tank", id: node.id });
          }}
          onNodeDrag={(_, node) => moveNode(node.id, node.position)}
          onNodeDragStop={(_, node) => void saveNodePosition(node.id, node.position)}
          onEdgeClick={(_, edge) => { if (edge.data?.gateId) setSelection({ kind: "gate", id: String(edge.data.gateId) }); }}>
          <Background color="#dce7df" gap={22} size={1} />
          <Controls showInteractive={false} />
          <MiniMap position="top-left" pannable zoomable nodeColor={(node) => node.type === "tank" ? "#4f9a72" : node.type === "gate" ? "#bd8051" : "#a8b6ad"} />
        </ReactFlow>
      </div> : <p className="empty-inline">No tanks configured.</p>}
    </section>

    <aside className="network-inspector">
      {tank ? <>
        <div className="inspector-heading"><span>Tank</span><h2>{tank.name}</h2></div>
        <div className="inspector-fields"><span>Water level</span><strong>{tankLevel?.text}{tankLevel?.stale ? " · last known" : ""}</strong><span>Priority</span><strong>{tank.priority === null ? "Unset" : tank.priority}</strong><span>Direction from {snapshot.tanks.find((item) => item.id === tank.parent_tank_id)?.name ?? "Main"}</span>
          <select aria-label={`Direction for ${tank.name}`} value={tank.direction ?? ""} disabled={busy || tank.id === main?.id}
            onChange={(event) => void setDirection(tank, (event.target.value || null) as TankDirection | null)}>
            <option value="">Automatic / unplaced</option>{(Object.keys(directions) as Direction[]).map((direction) => <option key={direction} value={direction}>{directions[direction].label}</option>)}
          </select></div>
        <h3>Sensors</h3>
        <div className="inspector-list">{snapshot.sensors.filter((sensor) => sensor.tank_id === tank.id).map((sensor) => <div className="inspector-row" key={sensor.adapter_name}><span>{sensor.adapter_name}</span><strong className={sensor.error ? "warn" : sensor.state ? "good" : ""}>{sensor.error ? "Missing" : sensor.state === null ? "Unknown" : sensor.state ? "Active" : "Inactive"}</strong></div>)}
          {!snapshot.sensors.some((sensor) => sensor.tank_id === tank.id) && <p className="empty-inline">No sensors assigned.</p>}</div>
        <h3>Gates</h3>
        <div className="inspector-list">{snapshot.gates.filter((item) => item.tank_id === tank.id || item.destination_tank_id === tank.id).map((item) => <button className="inspector-row interactive" key={item.id} onClick={() => setSelection({ kind: "gate", id: item.id })}><span>{item.name}</span><strong>{gateState(item, snapshot)}</strong></button>)}
          {!snapshot.gates.some((item) => item.tank_id === tank.id || item.destination_tank_id === tank.id) && <p className="empty-inline">No gates linked.</p>}</div>
      </> : gate ? <>
        <div className="inspector-heading"><span>Gate</span><h2>{gate.name}</h2></div>
        <div className="inspector-fields"><span>State</span><strong>{gateState(gate, snapshot)}</strong><span>Type</span><strong>{gate.type}</strong><span>HTTP Lever</span><strong>{gate.lever_name ?? "Missing lever"}</strong><span>Source</span><strong>{snapshot.tanks.find((item) => item.id === gate.tank_id)?.name ?? "Unassigned"}</strong><span>Destination</span><strong>{snapshot.tanks.find((item) => item.id === gate.destination_tank_id)?.name ?? "Outlet / unassigned"}</strong></div>
        {graph.assumptions[gate.id] && <p className="assumption-note">{graph.assumptions[gate.id]}</p>}
        {graph.unresolvedGateIds.includes(gate.id) && <p className="assumption-note">Location or endpoint is unknown.</p>}
      </> : anchor ? <>
        <div className="inspector-heading"><span>{anchor.data.kind === "assumed" ? "Assumed location" : "Terminal"}</span><h2>{anchor.data.label}</h2></div>
        {anchor.data.kind === "assumed" && <p className="assumption-note">Inferred from the two named output gates. This is not a saved tank.</p>}
      </> : <p className="empty-inline">Select a tank or gate.</p>}
      {graph.unplaced.length > 0 && <div className="inspector-extra"><h3>Unplaced tanks</h3>{graph.unplaced.map((item) => <button key={item.id} className="inspector-row interactive" onClick={() => setSelection({ kind: "tank", id: item.id })}><span>{item.name}</span><span>Set direction</span></button>)}</div>}
      {unassignedSensors.length > 0 && <details className="inspector-extra inspector-disclosure"><summary>Unassigned sensors <strong>{unassignedSensors.length}</strong></summary><div className="inspector-list">{unassignedSensors.map((sensor) => <label className="inspector-row sensor-assign" key={sensor.adapter_name}><span>{sensor.adapter_name}</span><select aria-label={`Tank for ${sensor.adapter_name}`} value="" disabled={busy} onChange={(event) => void assignSensor(sensor, event.target.value || null)}><option value="">No tank</option>{snapshot.tanks.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>)}</div></details>}
      {unknownGates.length > 0 && <details className="inspector-extra inspector-disclosure"><summary>Unknown gate locations <strong>{unknownGates.length}</strong></summary><div className="inspector-list">{unknownGates.map((item) => <button className="inspector-row interactive" key={item.id} onClick={() => setSelection({ kind: "gate", id: item.id })}><span>{item.name}</span><strong>{gateState(item, snapshot)}</strong></button>)}</div></details>}
    </aside>
    {assumedDraft && <div className="map-dialog-backdrop" onKeyDown={(event) => { if (event.key === "Escape" && !busy) setAssumedDraft(null); }} onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) setAssumedDraft(null); }}>
      <div className="map-dialog" role="dialog" aria-modal="true" aria-labelledby="assumed-title">
        <div className="map-dialog-heading"><div><span>Assumed location</span><h2 id="assumed-title">Create tank</h2></div><button type="button" className="map-tool-button" title="Close" aria-label="Close" disabled={busy} onClick={() => setAssumedDraft(null)}><X size={17} /></button></div>
        <form onSubmit={(event) => void saveAssumed(event)}>
          <div className="map-dialog-fields">
            <label>Tank name<input required autoFocus value={assumedDraft.name} onChange={(event) => setAssumedDraft({ ...assumedDraft, name: event.target.value })} /></label>
            <label>Priority<input type="number" min="0" step="1" placeholder="Unset" value={assumedDraft.priority} onChange={(event) => setAssumedDraft({ ...assumedDraft, priority: event.target.value })} /></label>
            <label>Relative to<select value={assumedDraft.parentTankId} required onChange={(event) => setAssumedDraft({ ...assumedDraft, parentTankId: event.target.value })}>{snapshot.tanks.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
            <label>Direction<select value={assumedDraft.direction} onChange={(event) => setAssumedDraft({ ...assumedDraft, direction: event.target.value as TankDirection })}>{(Object.keys(directions) as TankDirection[]).map((direction) => <option key={direction} value={direction}>{directions[direction].label}</option>)}</select></label>
            <label>Inlet gate<select value={assumedDraft.inletGateId} required onChange={(event) => setAssumedDraft({ ...assumedDraft, inletGateId: event.target.value })}>{snapshot.gates.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
            <label>Outlet gate<select value={assumedDraft.outletGateId} required onChange={(event) => setAssumedDraft({ ...assumedDraft, outletGateId: event.target.value })}>{snapshot.gates.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          </div>
          {snapshot.sensors.some((sensor) => !sensor.tank_id) && <fieldset className="map-dialog-sensors"><legend>Unassigned sensors</legend>{snapshot.sensors.filter((sensor) => !sensor.tank_id).map((sensor) => <label key={sensor.adapter_name}><input type="checkbox" checked={assumedDraft.sensorNames.includes(sensor.adapter_name)} onChange={(event) => setAssumedDraft({ ...assumedDraft, sensorNames: event.target.checked ? [...assumedDraft.sensorNames, sensor.adapter_name] : assumedDraft.sensorNames.filter((name) => name !== sensor.adapter_name) })} /> {sensor.adapter_name}</label>)}</fieldset>}
          {(draftError || error) && <p className="map-dialog-error" role="alert">{draftError || error}</p>}
          <div className="map-dialog-actions"><button type="button" className="text-button" disabled={busy} onClick={() => setAssumedDraft(null)}>Cancel</button><button className="command-button" disabled={busy}>Save tank</button></div>
        </form>
      </div>
    </div>}
  </div>;
}
