# Timberborn Control

## General Description

Timberborn Control is a local Python service and TypeScript dashboard for managing
Timberborn 1.0 automation. It reads boolean signals from in-game HTTP Adapters,
decides what linked HTTP Levers should do, and shows the current game state,
decisions, errors, and command history. The Overview tab shows an interactive
network graph; the former Overview dashboard is now the Status tab. The service
and dashboard bind to localhost.
All configuration and event history belongs to a named session. The active
session name appears at the top of the dashboard; use the Session tab to create
or load another one.

The documented game API exposes adapter states and lever on/off commands. It does
not expose raw water depth, flow rate, or contamination measurements, and it cannot
set an exact floodgate height. Configure thresholds and floodgate heights
in the game; this service interprets the resulting boolean signals and switches
the corresponding levers. See the [Timberborn API guide](https://timberborn.io/).

## Service Architecture

- `backend/timberborn_control/game_client.py` calls Timberborn's local API.
- `backend/timberborn_control/service.py` polls the game every 0.1 seconds by
  default, receives adapter webhooks, coordinates decisions, and sends lever
  commands. Webhooks cause an immediate refresh; polling discovers new adapters
  and recovers from missed events.
- `backend/timberborn_control/rules.py` evaluates the ordered, explicit
  adapter-to-lever rules. Optional adaptive flow control can only override an
  eligible input with a close/hold decision; it never opens a gate.
- `backend/timberborn_control/adaptive.py` tracks D65-to-D85 fill cycles,
  predicts cutoff deadlines, and holds an input closed after a missed target.
- `backend/timberborn_control/tagging.py` fills unambiguous tank associations
  from sensor and gate names without overwriting manual assignments.
- `backend/timberborn_control/api.py` exposes status and configuration to the
  Vite React dashboard. The dashboard polls the local backend every 2.5 seconds.
- `src/OverviewView.tsx` renders the tank and gate network with React Flow.
  Tank directions, sensor assignments, and manually dragged node positions are
  saved through the same configuration API as the rest of the session.
- `data/sessions.json` stores the active session and the list of named sessions.
  Each session has its own `data/sessions/<id>/config.json` for tanks, gates,
  sensors, rules, graph positions, adaptive settings, learned rates, and active
  cutoff holds, plus `events.jsonl` for its event history.
  Configurations and the session index are written atomically. The most recent
  100 events from the active session are returned in status snapshots.

On first startup after this change, existing `data/config.json` and
`data/events.jsonl` are moved into the Beaverton session. `TIMBERBORN_CONFIG_PATH`
now determines the parent directory for session storage and names the legacy
configuration file to migrate; `TIMBERBORN_EVENT_LOG_PATH` names the legacy event
log to migrate. These local data files are ignored by Git.

## Internal Data Model

| Concept | Stored fields and meaning |
| --- | --- |
| Tank | Name, optional integer priority (`0` highest), and optional cardinal direction (`N`, `E`, `S`, `W`) relative to Main or an explicitly selected parent tank. Source tanks do not need a priority. |
| Gate | 1m/2m/3m type, optional source and destination tanks, linked HTTP Lever, and whether lever on means open. Gates are descriptive; they never issue commands themselves. Exact heights stay configured in-game and are not read or set by this service. |
| Graph position | Optional `x`/`y` coordinates keyed by graph node ID in the session's `graph_positions` map. Positions affect only the Overview display, not automation. |
| Adaptive flow estimate | Per-input-gate fill rate in `%/second`, sample count, and update time. Learned from the D65-to-D85 interval and saved in the active session. |
| Sensor | A persisted HTTP Adapter identity, optional type (`Depth`, `Resource`, `Flow`, `BadWater`), optional semantic role, tank association, last known boolean state, and last-seen time. Its current value is boolean or unavailable. |
| Rule | ID, description, enabled flag (on by default), nested sensor conditions with AND/OR joins between adjacent items, and a target HTTP Lever state. An Inactive condition means NOT active. |
| AutomationDecision | The evaluated input states, selected rule, target lever state, and explanation. |
| ServiceEvent | A timestamped event. Successful automation commands include their full decision trace and persist to JSONL. |

Sensors are discovered from the game and saved even before they are classified.
On each successful game refresh and when saving configuration, a gate named
`North Intake` automatically links to an available lever named exactly
`HTTP North Intake`. A matching name replaces a stale lever assignment and is
persisted in the active session. Unmatched gates retain their current assignment;
gates with no assignment show a yellow **Missing lever** warning in Configuration.
Previously seen sensors stay visible if the game stops reporting them; the dashboard
shows a named error. The sensor row keeps showing its last known On/Off state and
timestamp even when the current game response is missing it. Delete a stale sensor
from Status
once the game is connected and no longer reports its adapter. Rules that reference
that adapter remain configured and are skipped until the signal returns. Assign
roles in the dashboard after
configuring the corresponding signal thresholds in Timberborn:

The service recognizes sensor names like `HTTP W D85`, `HTTP N Metalia D75`,
and `HTTP Main Tank D65`. It tags their type and associates them with a unique
matching tank, including the `N Metalia` to `Metalia N` alias. `C` names are
classified as BadWater. Names without a unique matching tank, such as
`HTTP NW Main C`, stay unassigned. Gate names can fill blank endpoints where
the direction is explicit: `W In` targets W, `N Main Diversion` starts at
Main N, and `S Main Output` starts at Main. Existing manual endpoints are not
overwritten. Each new association is logged as `config.auto_tagged`.

The Status Sensors panel also has **Auto Delete Missing Sensors**, off by default.
When enabled, each successful game refresh removes persisted sensors absent from
the adapter list and logs a `sensor.auto_deleted` event. It never deletes sensors
because the game API is unreachable. The setting is saved in the active session's
`config.json`.

- `needs_water`, `full`, and `reserve` are Depth sensor roles associated with tanks.
- `heavy_flow` is a Flow sensor role associated with the upstream/source tank.
- `badwater` is a BadWater sensor role. Assign it for classification; only
  explicit rules determine what active contamination does.

An active adapter signal must mean exactly what its role says. For example, a
`reserve` sensor should be on when the source tank has enough water to release.
Resource sensors are tracked and can participate in explicit rules.

### Network Overview

The graph places the tank named `Main` at its center. Cardinal tank names such
as `W` and suffixes such as `Main N` are positioned automatically; `Metalia N`
branches from `Metalia` when that parent is placed. Tanks without a resolvable
direction remain separate and amber until a direction is chosen in
the tank inspector or Configuration tab. A tank can also be placed relative to
another tank. These per-session choices are persisted.
Dashed links show inferred geography; every configured gate is a selectable
node colored by its reported open/closed state. Input and output terminals sit
at the top and bottom. Gates with unresolved endpoints remain visible in an
unknown-location lane instead of receiving guessed connections. The two
`S Metalia Output` gates are displayed with an **assumed tank** between them;
clicking it opens a dialog to create a real tank, choose its placement and
sensor assignments, and connect both gates in one saved change. Until saved,
the inferred tank does not change gate endpoints. Tank nodes show each sensor
name and its live active/inactive state; missing sensors have an amber marker.
Tank nodes also show the tightest range implied by their associated depth
signals, such as `0–65`,
`65–85`, or `>85` (`D65` means strictly above 65). Missing live readings use
last-known values and are marked stale. Click tanks, gates, or terminals for
details; pan and zoom the graph or use the minimap on desktop. Drag any box,
including gates and terminals, to reposition it. Its coordinates are saved to
the active session's `config.json` when released, so they survive refreshes and
reloads. Boxes without saved positions use automatic placement; the reset-layout
button clears saved positions and restores that placement. Straight lines show
gate connections, while dashed lines show inferred tank relationships. Moving
boxes does not change gate associations or issue lever commands.

## Rules Engine Overview

On each game refresh, the service evaluates the current adapter and lever states.
Enabled rules normally choose lever states. The beta adaptive controller is a
close-only exception for eligible input gates when enabled. Rules run in list order;
the first matching rule owns its lever for that cycle. Later matching rules for
that lever are marked shadowed. Rules with missing adapter inputs are skipped;
if none match for a lever, it holds its current state. Gate metadata, tank
priorities, and sensor roles have no automatic control behavior of their own.
Manual commands to rule-governed levers are blocked by the UI and API.

The rule editor expresses each rule as `When [conditions] Then [HTTP Lever]
[Active/Inactive]`. Each AND/OR join sits between adjacent sensors or groups,
so expressions such as `(A OR B) OR ((X AND Y) AND (Z AND D))` can be built
directly. Joins evaluate left to right within a group; parentheses from nested
groups set explicit precedence. Existing flat rules retain their original
meaning and acquire per-boundary joins when edited. A missing sensor anywhere
in a rule skips that rule, even
when another branch of an OR group is true. An Inactive sensor condition is
the equivalent of `NOT sensor active`.
The editor can group adjacent conditions, ungroup a group without deleting its
children, and reorder sibling conditions or groups. Rule-list up/down controls
change rule priority immediately; condition edits take effect when the rule is saved.

The service commands a lever only when its desired state differs from the game's
reported state. Every successful engine command emits an `automation.commanded`
event containing the evaluated inputs, rule, output, and reason. The dashboard
shows current rule decisions and the event history. Missing signals, command
errors, and failed event logging appear in the UI.
If the game accepts a command but continues reporting the old lever state, the
service shows that mismatch and retries at most once every 30 seconds.
Rules requiring an unknown contamination signal are skipped. Safety behavior
therefore depends on the conditions and ordering of the configured rules.

### 1. Priority Tank Transfers

Tank priority is optional descriptive metadata (`0` highest); it does not
automatically order water transfers. Express each desired transfer with explicit
sensor rules. Paired D85-on and D65-off rules can provide output hysteresis.
W's second inlet requires N Main D65 to be active for normal filling; its first
inlet also participates in overflow and contamination routing. The active
session's rules are authoritative when these priorities conflict.

### 2. Badwater Diversion

Contamination handling is encoded by explicit rules, not a global override.
In Beaverton, `N Main C` controls N Main Diversion and N Metalia Diversion;
`NW Main C` controls NW Main Diversion. Either contamination signal forces
W In 2 inactive; W Diversion and W In also respond to NW Main contamination.
Place contamination sensors upstream of the diversion point; blocked flow can
affect readings at a floodgate. See the
[Contamination Sensor documentation](https://timberborn.wiki.gg/wiki/Contamination_Sensor).

### 3. High Upstream Water

High-water handling must also be expressed in rules. Beaverton uses N Main
D85 to activate W Diversion and W In; NW Main Diversion now responds only to
NW Main C. Outside overflow or contamination diversion, W In fills W only
when N Main D65 is active and W D65 is inactive. W In 2 activates only while
N Main D65 is active, W D65 is inactive, and both upstream contamination
signals are inactive. W Out opens at W D85 and closes below W D65.

### Adaptive Flow Control (Beta)

The Configuration tab has an **Adaptive flow control** toggle, off by default,
and a shutoff target above 85% and at most 100% (default 95%). The controller
tracks fill cycles and learns rates even while the toggle is off. A gate is
eligible only when it is the sole configured input to a tank, has a linked
HTTP Lever governed by an enabled rule, and that tank has one D65 and one D85
sensor. Configuration shows each input's learned rate or eligibility error.
Beaverton's two W inputs are excluded because their individual contributions
cannot be measured from one pair of tank sensors.

Opening an eligible gate starts an open-duration timer. The 20% rate sample
starts when D65 becomes active while the gate is open and ends when D85 becomes
active; opening below D65 does not identify the starting water level. The
observed rate is `20 / elapsed_seconds` percent fill per second. Each new
sample updates the persisted estimate using 60% of the previous estimate and
40% of the new observation. A sample is not invented if the gate closes before
D85 is observed.

With beta enabled and a learned rate, the predicted cutoff is
`D65_time + (target_percent - 65) / fill_percent_per_second`. While D65 is
active, the controller closes the input at D85 or the predicted deadline,
whichever is observed first. D85 normally closes the gate before a 95% target;
the prediction acts as a fallback when that signal remains inactive too long.
If a required sensor disappears from the game response, adaptive control does
not start a new prediction or treat the missing reading as false; an existing
hold remains until D65 is confirmed inactive. If the
deadline arrives while D85 is still inactive, it logs `adaptive.warning`,
closes the gate, and persists a hold until D65 becomes inactive. That hold
survives a backend restart. Normal rules remain responsible for opening the
gate when filling may resume. The event log also records timer starts/stops,
rate updates (`adaptive.rate_updated`), and adaptive closes (`adaptive.closed`).
Timing uses wall-clock seconds; sensor transitions are observed through the
game API's polling and webhooks, so estimates can be affected by polling delay
or pausing the game. The controller never changes in-game threshold settings.

## Running Locally

Backend, from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
$env:PYTHONPATH="backend"
python -m timberborn_control
```

The backend listens at `http://127.0.0.1:8090`. Copy `.env.example` to `.env` to
change the game URL, polling interval, or data paths. Timberborn's API is expected
at `http://localhost:8080` by default.

Frontend:

```powershell
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to the backend.

Useful endpoints:

- `GET /api/status` returns game state, decisions, and recent events.
- `POST /api/sessions` with `{ "name": "New Town" }` creates and activates an
  empty session.
- `POST /api/sessions/{id}/load` activates an existing session. Status snapshots
  include the active session ID/name and available sessions.
- `POST /api/refresh` triggers a game refresh and decision pass.
- `PUT /api/config` saves tanks, gates, sensor metadata, rules, graph positions,
  and adaptive flow settings. Learned rates and cutoff holds are maintained by
  the service in the same session configuration.
- `DELETE /api/sensors/{name}` removes a persisted sensor absent from the game.
- `PUT /api/settings/sensors` with `{ "auto_delete_missing_sensors": true }` updates
  the automatic cleanup setting.
- `PUT /api/rules` saves the complete ordered rule list.
- `POST /api/levers/{name}` sends a manual on/off command only for levers not
  governed by an enabled rule; otherwise it returns HTTP 409.
- `GET` or `POST /api/timberborn/webhook/{name}/on` and `/off` receives adapter
  transitions. For `Reservoir High`, configure the on URL in the game as
  `http://127.0.0.1:8090/api/timberborn/webhook/Reservoir%20High/on` and the off
  URL with `/off` instead. Object names must be URL-encoded.

Run checks with `python -m pytest`, `python -m ruff check backend tests`, and
`npm run build`.
