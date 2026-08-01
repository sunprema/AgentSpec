"""Mermaid rendering: node shapes, edge grammar, and the failure layer."""

import pytest

from agentspec.graph import render_markdown, render_mermaid
from agentspec.parser import parse_file, parse_source


@pytest.fixture(scope="module")
def bookbank(fixtures_dir):
    return parse_file(fixtures_dir / "bookbank_routine.aspec.py")


def mermaid(module, **kwargs):
    task = module.tasks[module.root_task]
    return render_mermaid(module, task, **kwargs)


def test_bookbank_nodes_and_gates(bookbank):
    out = mermaid(bookbank)
    assert out.startswith("flowchart TD")
    assert '    workspace["workspace = ResolveWorkspace"]' in out
    # gates are dashed edges labeled with the condition
    assert '    workspace -. "workspace.resolved" .-> plugin' in out
    assert '    mark -. "mark.marked" .-> build' in out
    # the join: every prior result flows into alert
    for dep in ["workspace", "plugin", "issue", "build", "art", "notify"]:
        assert f"    {dep} --> alert" in out
    # dispatch inputs feed SelectIssue
    assert '    _inputs(["inputs: freeform_context"])' in out
    assert "    _inputs --> issue" in out


def test_bookbank_failure_layer(bookbank):
    plain = mermaid(bookbank)
    assert "_unwind" not in plain
    assert "retry" not in plain

    layered = mermaid(bookbank, failures=True)
    assert '    build -. "on failure: abort" .-> _unwind' in layered
    assert '    _unwind[/"saga unwind: undo in reverse order"/]' in layered
    assert "on failure: retry ×3 then declared default" in layered  # NotifyIssue
    assert "undo declared" in layered  # MarkStarted / GenerateBook


def test_fanout_shapes_and_edges(fixtures_dir):
    module = parse_file(fixtures_dir / "fanout_routine.aspec.py")
    out = mermaid(module)
    assert '    plans[["plans = TriageFinding ×N"]]' in out
    assert '    scan -- "each of scan.findings" --> plans' in out
    assert "    plans -- \"each of plans where p.action in ['page']\" --> pages" in out


def test_serialized_fanout_marker():
    module = parse_source(
        '''"""Doc."""
from pydantic import BaseModel
from agentspec import Task, Tool

class Out(BaseModel):
    ok: bool
    label: str

class Batch(BaseModel):
    items: list[Out]

class Scan(Task):
    """Scan."""
    returns: Batch

class Mut(Task):
    """Mutate one item."""
    v: str
    returns: Out
    tools = [Tool("git", ops=["push"], exclusive=True)]
    undo = "Revert."
    on_item_failure = "abort"

class R(Task):
    """Route."""
    returns: Out
    scan = Scan()
    work = [Mut(v=i.label) for i in scan.items]
'''
    )
    out = mermaid(module)
    assert "work = Mut ×N<br/>serialized per item" in out


def test_reserved_var_names_are_sanitized():
    module = parse_source(
        '''"""Doc."""
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
    end = W()
    after = W() if end.ok else None
'''
    )
    out = mermaid(module)
    assert '    end_["end = W"]' in out
    assert '    end_ -. "end.ok" .-> after' in out


def test_markdown_document(bookbank):
    doc = render_markdown(bookbank, failures=True)
    assert doc.startswith("# Execution graph — bookbank_routine.aspec.py")
    assert "## BookbankRun" in doc
    assert "```mermaid\nflowchart TD" in doc
    assert doc.rstrip().endswith("```")


def test_markdown_without_orchestrator(fixtures_dir):
    doc = render_markdown(parse_file(fixtures_dir / "shared_rules.aspec.py"))
    assert "No orchestrator" in doc
