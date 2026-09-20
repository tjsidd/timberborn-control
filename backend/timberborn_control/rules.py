from collections.abc import Mapping

from timberborn_control.models import (
    Adapter,
    Rule,
    RuleAction,
    RuleCondition,
    RuleEvaluation,
    RuleOperator,
    SensorCondition,
)


class RulesEngine:
    def evaluate(self, rules: list[Rule], adapters: list[Adapter]) -> list[RuleEvaluation]:
        adapter_states = {adapter.name: adapter.state for adapter in adapters}
        return [self._evaluate_one(rule, adapter_states) for rule in rules]

    def _evaluate_one(self, rule: Rule, adapter_states: Mapping[str, bool]) -> RuleEvaluation:
        if not rule.enabled:
            return RuleEvaluation(rule_id=rule.id, matched=False, skipped=True, reason="disabled")

        if not rule.adapter_names:
            return RuleEvaluation(
                rule_id=rule.id,
                matched=False,
                skipped=True,
                reason="no adapter conditions configured",
            )

        missing = [name for name in rule.adapter_names if name not in adapter_states]

        if missing:
            return RuleEvaluation(rule_id=rule.id, matched=False, skipped=True,
                                  reason=f"missing adapters: {', '.join(missing)}")
        if rule.condition is not None:
            matched = self._matches(rule.condition, adapter_states)
        else:
            checks = [adapter_states[name] is expected for name, expected in rule.when_adapters.items()]
            matched = all(checks) if rule.operator == RuleOperator.all else any(checks)
        return RuleEvaluation(rule_id=rule.id, matched=matched)

    def _matches(self, condition: RuleCondition, states: Mapping[str, bool]) -> bool:
        if isinstance(condition, SensorCondition):
            return states[condition.adapter_name] is condition.active
        checks = [self._matches(child, states) for child in condition.children]
        joins = condition.joins or [condition.operator] * (len(checks) - 1)
        matched = checks[0]
        for operator, check in zip(joins, checks[1:], strict=True):
            matched = matched and check if operator == RuleOperator.all else matched or check
        return matched


def desired_lever_state(action: RuleAction) -> bool:
    return action == RuleAction.switch_on
