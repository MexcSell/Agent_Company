import pytest

from agent_company import contracts
from agent_company.contracts import ContractError


def base(**kw):
    return {"key": "a", "title": "A", "role": "developer", "description": "do it", **kw}


def test_valid_contract_defaults():
    c = contracts.validate(base())
    assert c.priority == 5 and c.acceptance[0].type == "min_length"


def test_rejects_bad_key_and_regex():
    with pytest.raises(ContractError):
        contracts.validate(base(key="has space"))
    with pytest.raises(ContractError):
        contracts.validate(base(acceptance=[{"type": "regex", "pattern": "("}]))


def test_input_schema_enforced():
    schema = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}
    contracts.validate(base(inputs={"n": 3}, input_schema=schema))
    with pytest.raises(ContractError):
        contracts.validate(base(inputs={"n": "x"}, input_schema=schema))


def test_plan_detects_cycles_and_unknown_deps():
    with pytest.raises(ContractError, match="cycle"):
        contracts.validate_plan([base(key="a", depends_on=["b"]), base(key="b", depends_on=["a"])])
    with pytest.raises(ContractError, match="unknown"):
        contracts.validate_plan([base(key="a", depends_on=["zzz"])])
    with pytest.raises(ContractError, match="unique"):
        contracts.validate_plan([base(key="a"), base(key="a")])


def test_checks(tmp_path):
    c = contracts.validate(base(
        output_schema={"type": "object", "required": ["ok"]},
        acceptance=[{"type": "contains", "value": "hello"}, {"type": "regex", "pattern": r"VERDICT: PASS"},
                    {"type": "json_schema"}, {"type": "min_length", "value": 5}]))
    res = contracts.check(c, 'Hello there\nVERDICT: PASS\n```json\n{"ok": true}\n```', tmp_path)
    assert all(r["passed"] for r in res), res
    res = contracts.check(c, "nope", tmp_path)
    assert [r["passed"] for r in res] == [False, False, False, False]


def test_file_and_command_checks_stay_in_sandbox(env):
    from agent_company.config import settings

    wt = settings().worktrees_dir / "x"
    wt.mkdir(parents=True)
    (wt / "f.txt").write_text("1")
    c = contracts.validate(base(acceptance=[{"type": "file_exists", "path": "f.txt"},
                                            {"type": "file_exists", "path": "../../etc/passwd"},
                                            {"type": "command", "run": "test -f f.txt"}]))
    assert [r["passed"] for r in contracts.check(c, "", wt)] == [True, False, True]
    from agent_company.worktrees import WorktreeError

    with pytest.raises(WorktreeError):
        contracts.run_command("true", env, 10)


def test_extract_json_variants():
    assert contracts.extract_json('x {"a": 1} y') == {"a": 1}
    assert contracts.extract_json("```json\n[1, 2]\n```") == [1, 2]
    with pytest.raises(ValueError):
        contracts.extract_json("no json")


# --- G-R-01 task contract minimum fields -------------------------------------------------------------

def test_minimum_fields_are_optional_and_validated():
    c = contracts.validate(base())
    assert (c.in_scope, c.out_of_scope, c.risk_level, c.test_plan, c.deadline, c.requirement_keys,
            c.decision_topic) == ([], [], "medium", "", 0, [], "")
    assert c.budget.cost_usd == 0 and c.budget.turns == 0
    c = contracts.validate(base(out_of_scope=["tests/*"], risk_level="high", budget={"cost_usd": 0.5, "turns": 20},
                                deadline=300, requirement_keys=["S08-FR-02"], decision_topic="database"))
    assert c.budget.turns == 20 and c.deadline == 300
    for bad in ({"risk_level": "extreme"}, {"deadline": -1}, {"budget": {"cost_usd": -1}}):
        with pytest.raises(ContractError):
            contracts.validate(base(**bad))


def test_limits_fail_out_of_scope_changes_and_runs_over_budget():
    c = contracts.validate(base(out_of_scope=["tests/*", "docs"], budget={"cost_usd": 0.1}))
    rows = contracts.limits(c, ["src/a.py", "tests/test_a.py", "docs/x/y.md"], cost=0.25)
    scope, budget = rows
    assert not scope["passed"] and "tests/test_a.py" in scope["detail"] and "docs/x/y.md" in scope["detail"]
    assert "src/a.py" not in scope["detail"]
    assert not budget["passed"] and "$0.2500" in budget["detail"]
    assert all(r["passed"] for r in contracts.limits(c, ["src/a.py"], cost=0.05))
    assert contracts.limits(contracts.validate(base()), ["anything"], cost=9.0) == [], "no limits, no rows"


def test_findings_next_actions_and_recommendation_lines():
    out = ("Done.\nFINDING: a.py:3 | breaks on empty input | guard it\n- NEXT: add a regression test\n"
           "NEXT ACTION: update the docs\nRECOMMENDATION: PostgreSQL\nRECOMMENDATION: SQLite\n")
    assert contracts.findings(out) == ["a.py:3 | breaks on empty input | guard it"]
    assert contracts.next_actions(out) == ["add a regression test", "update the docs"]
    assert contracts.recommendation(out) == "SQLite", "the last recommendation counts"
    assert contracts.recommendation("none here") == ""
