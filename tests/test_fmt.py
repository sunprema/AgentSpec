"""Canonical formatting: ordering, comment travel, idempotence, CLI modes."""

import pytest

from agentspec.fmt import FormatError, format_source

FIXTURES = [
    "bookbank_routine.aspec.py",
    "minimal_good.aspec.py",
    "fanout_routine.aspec.py",
    "imports_main.aspec.py",
    "shared_rules.aspec.py",
    "bad_module_statement.aspec.py",
]


@pytest.mark.parametrize("fixture", FIXTURES)
def test_idempotence(fixture, fixtures_dir):
    source = (fixtures_dir / fixture).read_text()
    once = format_source(source, fixture)
    assert format_source(once, fixture) == once


def test_reorders_task_body_canonically():
    source = '''"""Doc."""
from pydantic import BaseModel
from agentspec import Task, Tool

class Out(BaseModel):
    ok: bool

class T(Task):
    """Do the thing."""
    meta = {"version": "1.0.0"}
    on_failure = "abort"
    returns: Out
    undo = "Reverse it."
    v: str
    tools = [Tool("bash", ops=["ls"])]
    constraints = [("Be safe", "why")]
'''
    formatted = format_source(source)
    order = [
        line.strip().split(" ")[0].rstrip(":")
        for line in formatted.splitlines()
        if line.startswith("    ") and not line.strip().startswith('"""')
    ]
    assert order == ["ok", "v", "returns", "tools", "constraints", "undo", "on_failure", "meta"]


def test_bind_order_is_preserved():
    source = '''"""Doc."""
from pydantic import BaseModel
from agentspec import Task

class Out(BaseModel):
    ok: bool

class W(Task):
    """W."""
    returns: Out

class R(Task):
    """Route."""
    returns: Out
    on_failure = "abort"
    first = W()
    second = W() if first.ok else None
'''
    formatted = format_source(source)
    first = formatted.index("first = W()")
    second = formatted.index("second = W()")
    on_failure = formatted.index('on_failure = "abort"')
    assert first < second < on_failure  # binds keep order; reserved attrs move after


def test_comments_travel_with_their_statement():
    source = '''"""Doc."""
from pydantic import BaseModel
from agentspec import Task

class Out(BaseModel):
    ok: bool

class T(Task):
    """Do."""
    # failure doctrine: never continue past a broken mutation
    on_failure = "abort"
    returns: Out
'''
    formatted = format_source(source)
    comment = formatted.index("failure doctrine")
    returns = formatted.index("returns: Out")
    on_failure = formatted.index('on_failure = "abort"')
    assert returns < comment < on_failure  # comment moved down with on_failure


def test_layout_is_normalized():
    source = '''"""Doc."""
from pydantic import BaseModel
from agentspec import Task

class Out(BaseModel):
    ok:bool

class T( Task ):
    """Do."""
    returns : Out
    on_failure = 'abort'
'''
    formatted = format_source(source)
    assert "ok: bool" in formatted
    assert "class T(Task):" in formatted
    assert 'on_failure = "abort"' in formatted


def test_syntax_error_raises():
    with pytest.raises(FormatError, match="syntax error"):
        format_source("def broken(:\n")


def test_already_canonical_fixture_is_stable(fixtures_dir):
    # minimal_good is written in canonical order: the only changes fmt may
    # make are ruff layout ones, and a second pass must be a no-op (covered
    # by idempotence); the statement order must survive untouched.
    source = (fixtures_dir / "minimal_good.aspec.py").read_text()
    formatted = format_source(source)
    triage = formatted.index("triage = TriageMessage")
    ticket = formatted.index("ticket = ")  # ruff may wrap the gated bind's rhs
    assert triage < ticket
