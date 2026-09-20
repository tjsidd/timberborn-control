from pydantic import ValidationError
from timberborn_control.models import Adapter, Configuration, Rule, RuleAction, RuleOperator
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


def test_nested_rule_matches_x_and_y_or_z() -> None:
    rule = Rule.model_validate({
        "id": "nested", "action": "switch_on", "lever": "Outlet",
        "condition": {"kind": "group", "operator": "all", "children": [
            {"kind": "sensor", "adapter_name": "X", "active": True},
            {"kind": "group", "operator": "any", "children": [
                {"kind": "sensor", "adapter_name": "Y", "active": True},
                {"kind": "sensor", "adapter_name": "Z", "active": False},
            ]},
        ]},
    })
    engine = RulesEngine()

    def matches(x: bool, y: bool, z: bool) -> bool:
        states = [Adapter(name=name, state=state) for name, state in (("X", x), ("Y", y), ("Z", z))]
        return engine.evaluate([rule], states)[0].matched

    assert matches(True, True, True)
    assert matches(True, False, False)
    assert not matches(True, False, True)
    assert not matches(False, True, False)
    assert rule.adapter_names == ["X", "Y", "Z"]
    assert Rule.model_validate(rule.model_dump()).condition == rule.condition


def test_nested_rule_skips_if_any_sensor_is_missing_even_if_or_branch_matches() -> None:
    rule = Rule.model_validate({
        "id": "nested", "action": "switch_on", "lever": "Outlet",
        "condition": {"kind": "group", "operator": "any", "children": [
            {"kind": "sensor", "adapter_name": "X", "active": True},
            {"kind": "sensor", "adapter_name": "Y", "active": True},
        ]},
    })
    result = RulesEngine().evaluate([rule], [Adapter(name="X", state=True)])[0]
    assert result.skipped is True
    assert result.matched is False
    assert result.reason == "missing adapters: Y"


def test_legacy_rules_remain_valid_in_session_configuration() -> None:
    config = Configuration.model_validate({"rules": [{
        "id": "legacy", "when_adapters": {"X": True, "Y": False},
        "operator": "any", "action": "switch_off", "lever": "Outlet",
    }]})
    assert config.rules[0].condition is None
    assert RulesEngine().evaluate(config.rules, [Adapter(name="X", state=False),
                                                 Adapter(name="Y", state=False)])[0].matched


def test_empty_nested_group_is_rejected() -> None:
    try:
        Rule.model_validate({"id": "empty", "action": "switch_on", "lever": "Outlet",
                             "condition": {"kind": "group", "children": []}})
    except ValidationError:
        pass
    else:
        raise AssertionError("empty condition group was accepted")


def test_joins_are_between_each_pair_of_conditions() -> None:
    rule = Rule.model_validate({
        "id": "joined", "action": "switch_on", "lever": "Outlet",
        "condition": {"kind": "group", "children": [
            {"kind": "sensor", "adapter_name": "A", "active": True},
            {"kind": "sensor", "adapter_name": "B", "active": True},
            {"kind": "sensor", "adapter_name": "C", "active": True},
        ], "joins": ["any", "all"]},
    })
    engine = RulesEngine()

    def matches(a: bool, b: bool, c: bool) -> bool:
        adapters = [Adapter(name=name, state=state) for name, state in
                    (("A", a), ("B", b), ("C", c))]
        return engine.evaluate([rule], adapters)[0].matched

    assert matches(True, False, True)
    assert not matches(True, False, False)
    assert matches(False, True, True)
    assert not matches(False, True, False)


def test_nested_groups_support_mixed_joins() -> None:
    def sensor(name: str) -> dict:
        return {"kind": "sensor", "adapter_name": name, "active": True}

    rule = Rule.model_validate({
        "id": "nested-joins", "action": "switch_on", "lever": "Outlet",
        "condition": {"kind": "group", "joins": ["any"], "children": [
            {"kind": "group", "joins": ["any"], "children": [sensor("A"), sensor("B")]},
            {"kind": "group", "joins": ["all"], "children": [
                {"kind": "group", "joins": ["all"], "children": [sensor("X"), sensor("Y")]},
                {"kind": "group", "joins": ["all"], "children": [sensor("Z"), sensor("D")]},
            ]},
        ]},
    })
    assert rule.adapter_names == ["A", "B", "X", "Y", "Z", "D"]
    assert RulesEngine().evaluate([rule], [
        Adapter(name=name, state=state) for name, state in
        (("A", False), ("B", False), ("X", True), ("Y", True), ("Z", True), ("D", True))
    ])[0].matched
    assert not RulesEngine().evaluate([rule], [
        Adapter(name=name, state=state) for name, state in
        (("A", False), ("B", False), ("X", True), ("Y", True), ("Z", True), ("D", False))
    ])[0].matched
    assert Rule.model_validate(rule.model_dump()).condition == rule.condition


def test_join_count_must_match_number_of_boundaries() -> None:
    try:
        Rule.model_validate({"id": "bad", "action": "switch_on", "lever": "Outlet",
                             "condition": {"kind": "group", "children": [
                                 {"kind": "sensor", "adapter_name": "A", "active": True},
                                 {"kind": "sensor", "adapter_name": "B", "active": True},
                             ], "joins": []}})
    except ValidationError:
        pass
    else:
        raise AssertionError("group with missing join was accepted")
