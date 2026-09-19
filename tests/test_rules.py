from timberborn_control.models import Adapter, Rule, RuleAction, RuleOperator
from timberborn_control.rules import RulesEngine


def test_rule_matches_all_adapter_conditions() -> None:
    engine = RulesEngine()
    rules = [
        Rule(
            id="open-bypass",
            when_adapters={"Badwater Detected": True, "Reservoir Low": False},
            operator=RuleOperator.all,
            action=RuleAction.switch_on,
            lever="Bypass",
        )
    ]
    adapters = [
        Adapter(name="Badwater Detected", state=True),
        Adapter(name="Reservoir Low", state=False),
    ]

    evaluations = engine.evaluate(rules, adapters)

    assert evaluations[0].matched is True


def test_disabled_rule_is_skipped() -> None:
    engine = RulesEngine()
    rules = [
        Rule(
            id="disabled",
            enabled=False,
            when_adapters={"Signal": True},
            action=RuleAction.switch_off,
            lever="Lever",
        )
    ]

    evaluations = engine.evaluate(rules, [Adapter(name="Signal", state=True)])

    assert evaluations[0].skipped is True
    assert evaluations[0].matched is False


def test_or_rule_matches_when_one_sensor_is_inactive() -> None:
    rule = Rule(
        id="open-outlet",
        when_adapters={"North D75": True, "North C": False},
        operator=RuleOperator.any,
        action=RuleAction.switch_on,
        lever="Outlet",
    )

    assert rule.enabled is True
    assert RulesEngine().evaluate([rule], [
        Adapter(name="North D75", state=False),
        Adapter(name="North C", state=False),
    ])[0].matched is True
    assert RulesEngine().evaluate([rule], [
        Adapter(name="North D75", state=False),
        Adapter(name="North C", state=True),
    ])[0].matched is False
