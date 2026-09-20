export type GameConnection = {
  ok: boolean;
  base_url: string;
  last_error: string | null;
  last_seen_at: string | null;
};

export type Lever = {
  name: string;
  state: boolean;
  springReturn: boolean;
};

export type Adapter = {
  name: string;
  state: boolean;
};

export type SensorType = "Depth" | "Resource" | "Flow" | "BadWater";
export type SensorRole = "needs_water" | "full" | "reserve" | "heavy_flow" | "badwater";
export type SensorConfig = { adapter_name: string; type: SensorType | null; role: SensorRole | null; tank_id: string | null; last_state: boolean | null; last_seen_at: string | null };
export type Sensor = SensorConfig & { state: boolean | null; error: string | null };
export type TankDirection = "N" | "NE" | "E" | "SE" | "S" | "SW" | "W" | "NW";
export type Tank = { id: string; name: string; priority: number | null; direction: TankDirection | null; parent_tank_id?: string | null };
export type GraphPosition = { x: number; y: number };
export type AdaptiveFlowEstimate = { fill_percent_per_second: number; updated_at: string; samples: number };
export type AdaptiveFlowStatus = { gate_id: string; gate_name: string; tank_name: string; fill_percent_per_second: number | null; opened_at: string | null; band_started_at: string | null; predicted_close_at: string | null; held_off: boolean; eligibility_error: string | null };
export type GateType = "1m" | "2m" | "3m";
export type Gate = {
  id: string;
  name: string;
  type: GateType;
  open_when_on: boolean;
  tank_id: string | null;
  destination_tank_id: string | null;
  lever_name: string | null;
};
export type Configuration = { auto_delete_missing_sensors: boolean; graph_positions: Record<string, GraphPosition>; adaptive_flow_enabled: boolean; adaptive_target_percent: number; adaptive_flow_estimates: Record<string, AdaptiveFlowEstimate>; tanks: Tank[]; gates: Gate[]; sensors: SensorConfig[]; rules: Rule[] };
export type SessionInfo = { id: string; name: string };

export type RuleCondition =
  | { kind: "sensor"; adapter_name: string; active: boolean }
  | { kind: "group"; operator: "all" | "any"; children: RuleCondition[]; joins?: Array<"all" | "any"> | null };

export type Rule = {
  id: string;
  enabled: boolean;
  description: string;
  when_adapters: Record<string, boolean>;
  operator: "all" | "any";
  condition?: RuleCondition | null;
  action: "switch_on" | "switch_off";
  lever: string;
};

export type RuleEvaluation = {
  rule_id: string;
  matched: boolean;
  skipped: boolean;
  reason: string | null;
  evaluated_at: string;
};

export type ServiceEvent = {
  kind: string;
  message: string;
  at: string;
  detail: Record<string, string | boolean | number | null>;
  decision: AutomationDecision | null;
};

export type AutomationDecision = {
  rule: string;
  inputs: Record<string, boolean | null>;
  lever_name: string | null;
  output_state: boolean | null;
  reason: string;
  evaluated_at: string;
};

export type Snapshot = {
  active_session_id: string;
  session_name: string;
  sessions: SessionInfo[];
  connection: GameConnection;
  auto_delete_missing_sensors: boolean;
  graph_positions: Record<string, GraphPosition>;
  adaptive_flow_enabled: boolean;
  adaptive_target_percent: number;
  adaptive_flow_estimates: Record<string, AdaptiveFlowEstimate>;
  adaptive_flow_statuses: AdaptiveFlowStatus[];
  config_error: string | null;
  automation_error: string | null;
  event_log_error: string | null;
  levers: Lever[];
  adapters: Adapter[];
  tanks: Tank[];
  gates: Gate[];
  sensors: Sensor[];
  rules: Rule[];
  last_rule_evaluations: RuleEvaluation[];
  decisions: AutomationDecision[];
  events: ServiceEvent[];
};
