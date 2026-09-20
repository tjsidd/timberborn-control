import asyncio
from collections import deque
from datetime import UTC, datetime

import httpx

from timberborn_control.adaptive import (
    AdaptiveCutoff,
    AdaptiveFlowController,
    FlowSample,
    eligible_inputs,
)
from timberborn_control.config import Settings
from timberborn_control.game_client import TimberbornClient
from timberborn_control.models import (
    Adapter,
    AutomationDecision,
    Configuration,
    GameConnection,
    Lever,
    Rule,
    RuleEvaluation,
    Sensor,
    SensorStatus,
    ServiceEvent,
    Snapshot,
)
from timberborn_control.rules import RulesEngine, desired_lever_state
from timberborn_control.storage import ConfigStore, EventStore, SessionStore
from timberborn_control.tagging import auto_tag


class TimberbornController:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = TimberbornClient(settings.game_base_url, settings.request_timeout_seconds)
        self.rules_engine = RulesEngine()
        self.session_store = SessionStore(settings.config_path, settings.event_log_path)
        config_path, event_path = self.session_store.paths(self.session_store.index.active_id)
        self.store = ConfigStore(config_path)
        self.event_store = EventStore(event_path)
        self.config_error: str | None = None
        self.automation_error: str | None = None
        self.event_log_error: str | None = None
        try:
            self.configuration = self.store.load()
        except (OSError, ValueError) as exc:
            self.configuration = Configuration()
            self.config_error = f"Could not load {config_path}: {exc}"
        self.connection = GameConnection(base_url=settings.game_base_url)
        self.levers: list[Lever] = []
        self.adapters: list[Adapter] = []
        self.rules: list[Rule] = self.configuration.rules
        self.last_rule_evaluations: list[RuleEvaluation] = []
        try:
            self.events: deque[ServiceEvent] = deque(self.event_store.recent(), maxlen=100)
        except (OSError, ValueError) as exc:
            self.events = deque(maxlen=100)
            self.event_log_error = f"Could not load event log {event_path}: {exc}"
        self.decisions: list[AutomationDecision] = []
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._last_sensor_save_at: datetime | None = None
        self._pending_commands: dict[str, tuple[bool, datetime]] = {}
        self.adaptive = AdaptiveFlowController()
        self.adaptive.reset(self.configuration.adaptive_flow_held_gate_ids)
        self._adaptive_timer_task: asyncio.Task[None] | None = None
        self._adaptive_deadline: datetime | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._poll_loop(), name="timberborn-poll-loop")

    async def stop(self) -> None:
        adaptive_task = self._adaptive_timer_task
        self._cancel_adaptive_timer()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if adaptive_task:
            try:
                await adaptive_task
            except asyncio.CancelledError:
                pass
        await self.client.close()

    async def refresh_once(self) -> Snapshot:
        await self._refresh()
        return self.snapshot()

    async def adapter_webhook(self, name: str, state: bool) -> Snapshot:
        self._event("adapter.webhook", f"{name} switched {'on' if state else 'off'}.",
                    {"adapter": name, "state": state})
        await self._refresh()
        return self.snapshot()

    def snapshot(self) -> Snapshot:
        adapter_states = {adapter.name: adapter.state for adapter in self.adapters}
        sensors = []
        for sensor in self.configuration.sensors:
            if self.connection.ok and sensor.adapter_name in adapter_states:
                state = adapter_states[sensor.adapter_name]
                error = None
            elif self.connection.last_error:
                state = None
                error = f"Cannot read '{sensor.adapter_name}': game API is unavailable."
            elif self.connection.ok:
                state = None
                error = f"No state received for '{sensor.adapter_name}' in the latest game response."
            else:
                state = None
                error = None
            sensors.append(SensorStatus(**sensor.model_dump(), state=state, error=error))
        return Snapshot(
            active_session_id=self.session_store.index.active_id,
            session_name=self.session_store.current().name,
            sessions=self.session_store.index.sessions,
            connection=self.connection,
            auto_delete_missing_sensors=self.configuration.auto_delete_missing_sensors,
            graph_positions=self.configuration.graph_positions,
            adaptive_flow_enabled=self.configuration.adaptive_flow_enabled,
            adaptive_target_percent=self.configuration.adaptive_target_percent,
            adaptive_flow_estimates=self.configuration.adaptive_flow_estimates,
            adaptive_flow_statuses=self.adaptive.statuses(self.configuration),
            config_error=self.config_error,
            automation_error=self.automation_error,
            event_log_error=self.event_log_error,
            levers=self.levers,
            adapters=self.adapters,
            tanks=self.configuration.tanks,
            gates=self.configuration.gates,
            sensors=sensors,
            rules=self.rules,
            last_rule_evaluations=self.last_rule_evaluations,
            decisions=self.decisions,
            events=list(self.events),
        )

    async def create_session(self, name: str) -> Snapshot:
        async with self._lock:
            session = self.session_store.create(name)
            self._load_session(session.id)
            self._event("session.created", f"Created session '{session.name}'.")
        await self._refresh()
        return self.snapshot()

    async def load_session(self, session_id: str) -> Snapshot:
        async with self._lock:
            self._load_session(session_id)
            self._event("session.loaded", f"Loaded session '{self.session_store.current().name}'.")
        await self._refresh()
        return self.snapshot()

    def _load_session(self, session_id: str) -> None:
        config_path, event_path = self.session_store.paths(session_id)
        configuration = ConfigStore(config_path).load()
        events = EventStore(event_path).recent()
        self.session_store.activate(session_id)
        self.store = ConfigStore(config_path)
        self.event_store = EventStore(event_path)
        self.configuration = configuration
        self.rules = configuration.rules
        self.events = deque(events, maxlen=100)
        self.last_rule_evaluations = []
        self.decisions = []
        self.config_error = None
        self.automation_error = None
        self.event_log_error = None
        self._last_sensor_save_at = None
        self._pending_commands.clear()
        self.adaptive.reset(configuration.adaptive_flow_held_gate_ids)
        self._cancel_adaptive_timer()

    async def replace_rules(self, rules: list[Rule]) -> None:
        await self.replace_configuration(Configuration.model_validate({
            **self.configuration.model_dump(), "rules": rules,
        }))

    async def replace_configuration(self, configuration: Configuration) -> None:
        async with self._lock:
            old_inputs = {item.gate.id: (item.gate.destination_tank_id, item.gate.lever_name,
                                          item.gate.open_when_on, item.low_sensor, item.high_sensor)
                          for item in eligible_inputs(self.configuration)}
            new_inputs = {item.gate.id: (item.gate.destination_tank_id, item.gate.lever_name,
                                          item.gate.open_when_on, item.low_sensor, item.high_sensor)
                          for item in eligible_inputs(configuration)}
            valid_estimates = {gate_id: estimate
                               for gate_id, estimate in self.configuration.adaptive_flow_estimates.items()
                               if gate_id in new_inputs and old_inputs.get(gate_id) == new_inputs[gate_id]}
            configuration = configuration.model_copy(update={
                "adaptive_flow_estimates": valid_estimates})
            reset_adaptive = (
                configuration.adaptive_flow_enabled != self.configuration.adaptive_flow_enabled
                or configuration.adaptive_target_percent != self.configuration.adaptive_target_percent
                or configuration.gates != self.configuration.gates
                or configuration.rules != self.configuration.rules
                or [(sensor.adapter_name, sensor.tank_id, sensor.type) for sensor in configuration.sensors]
                != [(sensor.adapter_name, sensor.tank_id, sensor.type) for sensor in self.configuration.sensors]
            )
            known_names = {sensor.adapter_name for sensor in self.configuration.sensors}
            active_names = {adapter.name for adapter in self.adapters}
            incoming = {
                sensor.adapter_name: sensor for sensor in configuration.sensors
                if sensor.adapter_name in known_names or sensor.adapter_name in active_names
            }
            for sensor in self.configuration.sensors:
                if sensor.adapter_name in incoming:
                    incoming[sensor.adapter_name].last_seen_at = sensor.last_seen_at
                    incoming[sensor.adapter_name].last_state = sensor.last_state
                else:
                    incoming[sensor.adapter_name] = sensor
            configuration = Configuration.model_validate({
                **configuration.model_dump(), "sensors": list(incoming.values()),
            })
            configuration = configuration.model_copy(update={
                "adaptive_flow_held_gate_ids": (
                    set(self.configuration.adaptive_flow_held_gate_ids)
                    if configuration.adaptive_flow_enabled else set()),
            })
            configuration, gate_mappings = self._match_gate_levers(
                configuration, self.levers if self.connection.ok else [],
            )
            configuration, tags = auto_tag(configuration)
            self.store.save(configuration)
            self.configuration = configuration
            self.rules = configuration.rules
            self._pending_commands.clear()
            if reset_adaptive:
                self.adaptive.reset(configuration.adaptive_flow_held_gate_ids)
                self._cancel_adaptive_timer()
            self.config_error = None
            self.last_rule_evaluations = self.rules_engine.evaluate(self.rules, self.adapters)
            self.decisions = []
            self._event("config.updated", "Configuration saved.")
            self._log_gate_mappings(gate_mappings)
            self._log_tags(tags)

    async def delete_sensor(self, name: str) -> Snapshot:
        async with self._lock:
            if not any(sensor.adapter_name == name for sensor in self.configuration.sensors):
                raise KeyError(name)
            if not self.connection.ok:
                raise ValueError("Cannot verify stale sensors while the game API is unavailable.")
            if any(adapter.name == name for adapter in self.adapters):
                raise ValueError(f"Sensor '{name}' is still reported by the game.")
            updated = Configuration.model_validate({
                **self.configuration.model_dump(),
                "sensors": [sensor for sensor in self.configuration.sensors
                            if sensor.adapter_name != name],
            })
            self.store.save(updated)
            self.configuration = updated
            self.last_rule_evaluations = self.rules_engine.evaluate(self.rules, self.adapters)
            self.decisions = []
            self._event("sensor.deleted", f"Removed stale sensor '{name}'.", {"adapter": name})
            return self.snapshot()

    async def set_auto_delete_missing_sensors(self, enabled: bool) -> Snapshot:
        async with self._lock:
            updated = self.configuration.model_copy(update={"auto_delete_missing_sensors": enabled})
            self.store.save(updated)
            self.configuration = updated
            self.config_error = None
            self._event("sensor.settings_updated",
                        f"Auto Delete Missing Sensors {'enabled' if enabled else 'disabled'}.")
        if enabled:
            await self._refresh()
        return self.snapshot()

    async def switch_lever(self, name: str, state: bool) -> None:
        if any(rule.enabled and rule.lever == name for rule in self.rules):
            raise ValueError(f"Lever '{name}' is controlled by an enabled rule.")
        self._pending_commands.pop(name, None)
        if state:
            await self.client.switch_on(name)
        else:
            await self.client.switch_off(name)
        self._event("lever.commanded", f"Set {name} to {'on' if state else 'off'}.")
        await self._refresh()

    async def set_lever_color(self, name: str, color_hex: str) -> None:
        await self.client.set_color(name, color_hex)
        self._event("lever.colored", f"Set {name} color to {color_hex}.")

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._refresh()
            except Exception as exc:  # noqa: BLE001
                self._mark_disconnected(str(exc))
            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def _refresh(self) -> None:
        async with self._lock:
            try:
                levers, adapters = await asyncio.gather(
                    self.client.list_levers(),
                    self.client.list_adapters(),
                )
            except (httpx.HTTPError, ValueError) as exc:
                self._mark_disconnected(str(exc))
                return

            self.levers = levers
            previous_names = {adapter.name for adapter in self.adapters}
            self.adapters = adapters
            now = datetime.now(UTC)
            adapter_names = {adapter.name for adapter in adapters}
            adapter_states = {adapter.name: adapter.state for adapter in adapters}
            existing_names = {sensor.adapter_name for sensor in self.configuration.sensors}
            changed_states = {
                sensor.adapter_name for sensor in self.configuration.sensors
                if sensor.adapter_name in adapter_states
                and sensor.last_state is not adapter_states[sensor.adapter_name]
            }
            auto_deleted = (
                existing_names - adapter_names
                if self.configuration.auto_delete_missing_sensors else set()
            )
            sensors = [
                sensor.model_copy(update={"last_seen_at": now,
                                          "last_state": adapter_states[sensor.adapter_name]})
                if sensor.adapter_name in adapter_names else sensor
                for sensor in self.configuration.sensors
                if sensor.adapter_name not in auto_deleted
            ]
            sensors.extend(Sensor(adapter_name=adapter.name, last_state=adapter.state,
                                  last_seen_at=now)
                           for adapter in adapters if adapter.name not in existing_names)
            mapped_configuration, gate_mappings = self._match_gate_levers(
                self.configuration, levers,
            )
            tagged_configuration, tags = auto_tag(mapped_configuration.model_copy(update={"sensors": sensors}))
            if (tagged_configuration != self.configuration or gate_mappings or tags or previous_names - adapter_names
                    or (self.config_error is not None and sensors)):
                updated = Configuration.model_validate(tagged_configuration.model_dump())
                save_due = (
                    adapter_names - existing_names or previous_names - adapter_names
                    or changed_states
                    or auto_deleted
                    or gate_mappings
                    or tags
                    or self.config_error is not None
                    or self._last_sensor_save_at is None
                    or (now - self._last_sensor_save_at).total_seconds() >= 60
                )
                if save_due:
                    try:
                        self.store.save(updated)
                    except OSError as exc:
                        self.config_error = f"Could not persist discovered sensors: {exc}"
                    else:
                        self._last_sensor_save_at = now
                        self.config_error = None
                        for name in sorted(auto_deleted):
                            self._event("sensor.auto_deleted",
                                        f"Automatically removed missing sensor '{name}'.",
                                        {"adapter": name})
                        self._log_gate_mappings(gate_mappings)
                        self._log_tags(tags)
                if not ((auto_deleted or gate_mappings or tags) and self.config_error is not None):
                    self.configuration = updated
            self.connection = GameConnection(
                ok=True,
                base_url=self.settings.game_base_url,
                last_seen_at=now,
            )
            observation = self.adaptive.observe(
                self.configuration, adapter_states,
                {lever.name: lever.state for lever in levers}, now)
            if self.adaptive.held_off != self.configuration.adaptive_flow_held_gate_ids:
                self._save_adaptive_holds()
            for log in observation.logs:
                self._event(log.kind, log.message, log.detail)
            for sample in observation.samples:
                self._save_adaptive_sample(sample)
            self.last_rule_evaluations = self.rules_engine.evaluate(self.rules, self.adapters)
            self.automation_error = None
            self.decisions = []
            await self._apply_decisions(observation.cutoffs)
            self._schedule_adaptive_deadline(observation.next_deadline)

    def _save_adaptive_sample(self, sample: FlowSample) -> None:
        estimates = {**self.configuration.adaptive_flow_estimates,
                     sample.gate.id: sample.estimate}
        updated = self.configuration.model_copy(update={"adaptive_flow_estimates": estimates})
        try:
            self.store.save(updated)
        except OSError as exc:
            self.config_error = f"Could not persist adaptive flow rate for {sample.gate.name}: {exc}"
            self._event("adaptive.error", self.config_error, {"gate": sample.gate.name})
            return
        self.configuration = updated
        self.config_error = None
        self._event(
            "adaptive.rate_updated",
            f"{sample.gate.name}: learned {sample.estimate.fill_percent_per_second:.3f}% fill/s "
            f"from D65 to D85 in {sample.elapsed_seconds:.2f}s.",
            {"gate": sample.gate.name,
             "observed_percent_per_second": sample.observed_rate,
             "fill_percent_per_second": sample.estimate.fill_percent_per_second,
             "elapsed_seconds": sample.elapsed_seconds,
             "samples": sample.estimate.samples},
        )

    def _save_adaptive_holds(self) -> None:
        updated = self.configuration.model_copy(update={
            "adaptive_flow_held_gate_ids": set(self.adaptive.held_off)})
        try:
            self.store.save(updated)
        except OSError as exc:
            self.config_error = f"Could not persist adaptive hold state: {exc}"
            self._event("adaptive.error", self.config_error)
        else:
            self.configuration = updated
            self.config_error = None

    def _cancel_adaptive_timer(self) -> None:
        task = self._adaptive_timer_task
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
        self._adaptive_timer_task = None
        self._adaptive_deadline = None

    def _schedule_adaptive_deadline(self, deadline: datetime | None) -> None:
        if not self.configuration.adaptive_flow_enabled or deadline is None:
            self._cancel_adaptive_timer()
            return
        if (self._adaptive_timer_task and not self._adaptive_timer_task.done()
                and self._adaptive_deadline == deadline):
            return
        self._cancel_adaptive_timer()
        self._adaptive_deadline = deadline
        self._adaptive_timer_task = asyncio.create_task(
            self._refresh_at_deadline(deadline), name="timberborn-adaptive-deadline")

    async def _refresh_at_deadline(self, deadline: datetime) -> None:
        try:
            await asyncio.sleep(max(0, (deadline - datetime.now(UTC)).total_seconds()))
            self._adaptive_timer_task = None
            self._adaptive_deadline = None
            await self._refresh()
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            self.automation_error = f"Adaptive deadline refresh failed: {exc}"
            self._event("adaptive.error", self.automation_error)

    @staticmethod
    def _match_gate_levers(
        configuration: Configuration, levers: list[Lever]
    ) -> tuple[Configuration, list[tuple[str, str, str | None]]]:
        available = {lever.name for lever in levers}
        mappings = []
        gates = []
        for gate in configuration.gates:
            expected = f"HTTP {gate.name}"
            if expected in available and gate.lever_name != expected:
                mappings.append((gate.name, expected, gate.lever_name))
                gates.append(gate.model_copy(update={"lever_name": expected}))
            else:
                gates.append(gate)
        return configuration.model_copy(update={"gates": gates}), mappings

    def _log_gate_mappings(self, mappings: list[tuple[str, str, str | None]]) -> None:
        for gate_name, lever_name, previous in mappings:
            self._event("gate.lever_mapped", f"Mapped gate '{gate_name}' to '{lever_name}'.",
                        {"gate": gate_name, "lever": lever_name, "previous_lever": previous})

    def _log_tags(self, tags: list[tuple[str, str, str]]) -> None:
        for kind, name, tank in tags:
            self._event("config.auto_tagged", f"Tagged {kind} '{name}' to tank '{tank}'.",
                        {"kind": kind, "name": name, "tank": tank})

    async def _apply_decisions(self, cutoffs: list[AdaptiveCutoff]) -> None:
        lever_states = {lever.name: lever.state for lever in self.levers}
        rules_by_id = {rule.id: rule for rule in self.rules}
        adaptive_gates = {item.gate.lever_name: item.gate for item in eligible_inputs(self.configuration)}
        chosen_rules: dict[str, str] = {}

        for evaluation in self.last_rule_evaluations:
            if not evaluation.matched:
                continue
            rule = rules_by_id[evaluation.rule_id]
            if rule.lever in chosen_rules:
                evaluation.reason = f"shadowed by rule {chosen_rules[rule.lever]}"
                continue
            chosen_rules[rule.lever] = rule.id
            self.decisions.append(AutomationDecision(
                rule=rule.id,
                inputs={name: next((a.state for a in self.adapters if a.name == name), None)
                        for name in rule.adapter_names},
                lever_name=rule.lever,
                output_state=desired_lever_state(rule.action),
                reason=rule.description or "Adapter conditions matched.",
            ))

        flush_active = any(adapter.name == "HTTP Flush" and adapter.state for adapter in self.adapters)
        for cutoff in cutoffs:
            lever_name = cutoff.gate.lever_name
            target_state = not cutoff.gate.open_when_on
            previous = next((decision for decision in self.decisions
                             if decision.lever_name == lever_name), None)
            if flush_active and previous and previous.output_state is cutoff.gate.open_when_on:
                continue
            if previous and previous.output_state is target_state:
                continue
            if previous:
                self.decisions.remove(previous)
                evaluation = next((item for item in self.last_rule_evaluations
                                   if item.rule_id == previous.rule), None)
                if evaluation:
                    evaluation.reason = "overridden by adaptive flow cutoff"
            self.decisions.append(AutomationDecision(
                rule="adaptive-flow-cutoff",
                inputs={cutoff.low_sensor: next((adapter.state for adapter in self.adapters
                                                 if adapter.name == cutoff.low_sensor), None),
                        cutoff.high_sensor: next((adapter.state for adapter in self.adapters
                                                  if adapter.name == cutoff.high_sensor), None)},
                lever_name=lever_name,
                output_state=target_state,
                reason=cutoff.reason,
            ))

        for decision in self.decisions:
            name = decision.lever_name
            target_state = decision.output_state
            if name is None or target_state is None:
                continue
            if name not in lever_states:
                self.automation_error = f"Rule {decision.rule}: target lever '{name}' was not found in the game."
                continue
            pending = self._pending_commands.get(name)
            if lever_states[name] is target_state:
                self._pending_commands.pop(name, None)
                continue
            if pending is not None and pending[0] is target_state:
                elapsed = (datetime.now(UTC) - pending[1]).total_seconds()
                retry_after = 2 if decision.rule == "adaptive-flow-cutoff" else 30
                if elapsed < retry_after:
                    if elapsed >= min(5, retry_after / 2):
                        self.automation_error = (
                            f"Rule {decision.rule}: game still reports '{name}' "
                            f"{'off' if target_state else 'on'} after command; retrying in "
                            f"{max(1, int(retry_after - elapsed))}s."
                        )
                    continue
            try:
                if target_state:
                    await self.client.switch_on(name)
                else:
                    await self.client.switch_off(name)
            except httpx.HTTPError as exc:
                self.automation_error = f"Rule {decision.rule}: could not switch '{name}': {exc}"
                self._event("automation.error", self.automation_error, decision=decision)
                continue

            self._pending_commands[name] = (target_state, datetime.now(UTC))
            lever_states[name] = target_state
            adaptive_gate = adaptive_gates.get(name)
            if adaptive_gate:
                timer_log = self.adaptive.command(
                    adaptive_gate, target_state is adaptive_gate.open_when_on, datetime.now(UTC))
                if timer_log:
                    self._event(timer_log.kind, timer_log.message, timer_log.detail)
            input_text = ", ".join(
                f"{sensor}={str(value).lower() if value is not None else 'missing'}"
                for sensor, value in decision.inputs.items()
            ) or "none"
            self._event(
                "automation.commanded",
                f"Rule {decision.rule}: inputs [{input_text}]; set {name} to "
                f"{'on' if target_state else 'off'}. {decision.reason}",
                decision=decision,
            )
            if decision.rule == "adaptive-flow-cutoff":
                self._event("adaptive.closed", f"Adaptive flow closed {adaptive_gate.name if adaptive_gate else name}: "
                            f"{decision.reason}", {"gate": adaptive_gate.name if adaptive_gate else name},
                            decision=decision)

    def _mark_disconnected(self, error: str) -> None:
        was_ok = self.connection.ok
        self.adaptive.reset(self.configuration.adaptive_flow_held_gate_ids)
        self._cancel_adaptive_timer()
        self.connection = GameConnection(
            ok=False,
            base_url=self.settings.game_base_url,
            last_error=error,
            last_seen_at=self.connection.last_seen_at,
        )
        if was_ok or not self.events:
            self._event("game.disconnected", "Timberborn API is not reachable.", {"error": error})

    def _event(
        self,
        kind: str,
        message: str,
        detail: dict[str, str | bool | int | float | None] | None = None,
        decision: AutomationDecision | None = None,
    ) -> None:
        event = ServiceEvent(kind=kind, message=message, detail=detail or {}, decision=decision)
        self.events.append(event)
        try:
            self.event_store.append(event)
        except OSError as exc:
            self.event_log_error = f"Could not append event log {self.settings.event_log_path}: {exc}"
