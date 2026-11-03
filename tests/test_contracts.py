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
