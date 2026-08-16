"""The op risk lattice (spec 2.4): Op("name", risk=...) parsing, the
AS047/AS048 lint rules, and risk escalation in diff."""

from agentspec.diff import diff_modules
from agentspec.lint import lint_module
from agentspec.parser import parse_source

HEADER = '''"""Doc."""
from pydantic import BaseModel
from agentspec import Task, Tool, Op, Rule, Enum

class Out(BaseModel):
    ok: bool
    label: str

'''


def parse(body: str):
    return parse_source(HEADER + body)


def lint(body: str, ignore: tuple[str, ...] = ("AS033", "AS045")):
    return [d for d in lint_module(parse(body)) if d.code not in ignore]


def codes(diags):
    return {d.code for d in diags}


# -- parsing -----------------------------------------------------------------


def test_op_parses_with_risk_and_bare_strings_default_to_mutate():
    module = parse('''class T(Task):
    """Do."""
    returns: Out
    tools = [Tool("git", ops=["add", Op("push", risk="irreversible"), Op("log", risk="read")])]
    on_failure = "abort"
''')
    assert module.errors == []
    (tool,) = module.tasks["T"].tools
    assert tool.ops == ["add", "push", "log"]
    assert tool.risk_of("add") == "mutate"  # the conservative default
    assert tool.risk_of("push") == "irreversible"
    assert tool.risk_of("log") == "read"
    assert tool.max_risk() == "irreversible"
    assert tool.op_display() == ["add", "push (irreversible)", "log (read)"]


def test_op_rejects_bad_risk_and_bad_shape():
    module = parse('''class T(Task):
    """Do."""
    returns: Out
    tools = [Tool("git", ops=[Op("push", risk="scary"), Op(1)])]
    on_failure = "abort"
''')
    assert [d.code for d in module.errors] == ["P007", "P007"]


# -- lint --------------------------------------------------------------------


def test_as047_irreversible_op_without_undo():
    body = '''class T(Task):
    """Do."""
    returns: Out
    tools = [Tool("aws", ops=[Op("s3 rm", risk="irreversible")])]
    on_failure = "abort"
'''
    diags = lint(body)
    assert codes(diags) == {"AS047"}
    assert "aws:s3 rm" in diags[0].message
    with_undo = body.replace(
        'on_failure = "abort"', 'undo = "restore from versioning"\n    on_failure = "abort"'
    )
    assert lint(with_undo) == []


def test_as048_concurrent_steps_sharing_a_mutating_op():
    body = '''class A(Task):
    """Comment."""
    returns: Out
    tools = [Tool("gh", ops=["issue comment"])]
    on_failure = "abort"

class B(Task):
    """Also comment."""
    v: str
    returns: Out
    tools = [Tool("gh", ops=["issue comment"])]
    on_failure = "abort"

class R(Task):
    """Route."""
    returns: Out
    a = A()
    b = B(v="x")
    on_failure = "abort"
'''
    diags = lint(body)
    assert codes(diags) == {"AS048"}
    assert "'a' and 'b'" in diags[0].message


def test_as048_silent_when_ordered_read_only_or_exclusive():
    ordered = lint('''class A(Task):
    """Comment."""
    returns: Out
    tools = [Tool("gh", ops=["issue comment"])]
    on_failure = "abort"

class B(Task):
    """Also comment."""
    v: str
    returns: Out
    tools = [Tool("gh", ops=["issue comment"])]
    on_failure = "abort"

class R(Task):
    """Route."""
    returns: Out
    a = A()
    b = B(v=a.label)
    on_failure = "abort"
''')
    assert "AS048" not in codes(ordered)

    read_only = lint('''class A(Task):
    """Look."""
    returns: Out
    tools = [Tool("gh", ops=[Op("issue list", risk="read")])]
    on_failure = "abort"

class B(Task):
    """Also look."""
    v: str
    returns: Out
    tools = [Tool("gh", ops=[Op("issue list", risk="read")])]
    on_failure = "abort"

class R(Task):
    """Route."""
    returns: Out
    a = A()
    b = B(v="x")
    on_failure = "abort"
''')
    assert "AS048" not in codes(read_only)

    exclusive = lint('''class A(Task):
    """Comment."""
    returns: Out
    tools = [Tool("gh", ops=["issue comment"], exclusive=True)]
    on_failure = "abort"

class B(Task):
    """Also comment."""
    v: str
    returns: Out
    tools = [Tool("gh", ops=["issue comment"])]
    on_failure = "abort"

class R(Task):
    """Route."""
    returns: Out
    a = A()
    b = B(v="x")
    on_failure = "abort"
''')
    assert "AS048" not in codes(exclusive)


# -- diff --------------------------------------------------------------------

DIFF_BODY = '''class T(Task):
    """Do."""
    returns: Out
    tools = [Tool("git", ops=[Op("push", risk="RISK")])]
    undo = "Revert."
    on_failure = "abort"
'''


def test_diff_reports_risk_movement_on_the_lattice():
    old = parse(DIFF_BODY.replace("RISK", "read"))
    new = parse(DIFF_BODY.replace("RISK", "irreversible"))

    raised = [c for c in diff_modules(old, new) if c.code == "op-risk-raised"]
    assert len(raised) == 1
    assert raised[0].direction == "widens"
    assert "read -> irreversible" in raised[0].detail

    lowered = [c for c in diff_modules(new, old) if c.code == "op-risk-lowered"]
    assert len(lowered) == 1
    assert lowered[0].direction == "narrows"


def test_diff_untagging_an_op_is_the_mutate_default_not_a_removal():
    old = parse(DIFF_BODY.replace("RISK", "read"))
    new = parse(DIFF_BODY.replace('Op("push", risk="RISK")', '"push"'))
    changes = diff_modules(old, new)
    assert [c.code for c in changes if c.category == "tool"] == ["op-risk-raised"]
