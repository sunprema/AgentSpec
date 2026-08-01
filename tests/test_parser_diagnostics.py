"""Structured parse errors (P0xx), cross-file imports, and micro-constructs."""

from agentspec.parser import parse_file, parse_source

HEADER = '''"""Doc."""
from pydantic import BaseModel
from agentspec import Task, Tool, Enum, Retry, Escalate

class Out(BaseModel):
    ok: bool

'''


def codes(module):
    return {d.code for d in module.diagnostics}


def test_syntax_error_is_p001():
    module = parse_source("def broken(:\n")
    assert codes(module) == {"P001"}
    assert module.tasks == {}


def test_bad_module_statements(fixtures_dir):
    module = parse_file(fixtures_dir / "bad_module_statement.aspec.py")
    assert not module.ok
    # import os → P006, computed constant → P004, def helper → P002
    assert {"P002", "P004", "P006"} <= codes(module)
    # the conformant parts still parse
    assert set(module.tasks) == {"DoThing"}
    assert set(module.schemas) == {"Result"}


def test_cross_file_import(fixtures_dir):
    module = parse_file(fixtures_dir / "imports_main.aspec.py")
    assert module.errors == []
    assert module.imports[-1].kind == "spec"
    assert module.imports[-1].resolved_path.endswith("shared_rules.aspec.py")
    rules = module.tasks["DoWork"].constraints
    assert len(rules) == 3
    assert rules[0].source == "SAFETY"
    assert rules[1].severity == "must"
    assert rules[2].why == "auditability"


def test_unresolvable_import():
    module = parse_source('"""D."""\nfrom nowhere import X\n')
    assert "P006" in codes(module)


def test_escalate_with_nested_then():
    module = parse_source(
        HEADER
        + '''class T(Task):
    """Do it."""
    returns: Out
    on_failure = Escalate(channel="ops-pager", timeout_s=900,
                          then=Retry(max=2, backoff_s=10, then="abort"))
'''
    )
    assert module.errors == []
    policy = module.tasks["T"].on_failure
    assert policy.kind == "escalate"
    assert policy.channel == "ops-pager"
    assert policy.timeout_s == 900
    assert policy.then.kind == "retry"
    assert policy.then.max == 2
    assert policy.then.then.kind == "abort"


def test_rich_filter_is_recorded_invalid():
    module = parse_source(
        HEADER
        + '''class W(Task):
    """Work one item."""
    item_id: str
    returns: Out

class R(Task):
    """Route items."""
    returns: Out
    scan = W(item_id="seed")
    work = [W(item_id=i.id) for i in scan.items if i.id.startswith("x")]
'''
    )
    filt = module.tasks["R"].bind("work").fanout.filter
    assert not filt.valid
    assert "startswith" in filt.raw


def test_gate_with_non_none_else_is_p010():
    module = parse_source(
        HEADER
        + '''class W(Task):
    """Work."""
    returns: Out

class R(Task):
    """Route."""
    returns: Out
    first = W()
    second = W() if first.ok else first
'''
    )
    assert "P010" in codes(module)


def test_unknown_class_base_is_p003():
    module = parse_source(HEADER + "class Odd(dict):\n    pass\n")
    assert "P003" in codes(module)


def test_unknown_task_attr_is_recorded_not_bound():
    module = parse_source(
        HEADER
        + '''class T(Task):
    """Do it."""
    returns: Out
    retries = 3
'''
    )
    task = module.tasks["T"]
    assert task.pipeline == []
    assert [a.name for a in task.unknown_attrs] == ["retries"]
