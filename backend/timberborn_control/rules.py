from collections.abc import Mapping

from timberborn_control.models import Adapter, Rule, RuleAction, RuleEvaluation, RuleOperator


class RulesEngine:
    def evaluate(self, rules: list[Rule], adapters: list[Adapter]) -> list[RuleEvaluation]:
        adapter_states = {adapter.name: adapter.state for adapter in adapters}
        return [self._evaluate_one(rule, adapter_states) for rule in rules]

    def _evaluate_one(self, rule: Rule, adapter_states: Mapping[str, bool]) -> RuleEvaluation:
        if not rule.enabled:
            return RuleEvaluation(rule_id=rule.id, matched=False, skipped=True, reason="disabled")

        if not rule.when_adapters:
            return RuleEvaluation(
                rule_id=rule.id,
                matched=False,
                skipped=True,
                reason="no adapter conditions configured",
            )

        checks = []
        missing = []
        for name, expected_state in rule.when_adapters.items():
            if name not in adapter_states:
                missing.append(name)
                checks.append(False)
            else:
                checks.append(adapter_states[name] is expected_state)

        if missing:
            return RuleEvaluation(rule_id=rule.id, matched=False, skipped=True,
                                  reason=f"missing adapters: {', '.join(missing)}")
        matched = all(checks) if rule.operator == RuleOperator.all else any(checks)
        return RuleEvaluation(rule_id=rule.id, matched=matched)


def desired_lever_state(action: RuleAction) -> bool:
    return action == RuleAction.switch_on
