"""One test per lint rule, driven by inline spec snippets."""

from agentspec.lint import lint_module
from agentspec.parser import parse_source

HEADER = '''"""Doc."""
from pydantic import BaseModel
from agentspec import Task, Tool, Enum, Retry, Escalate

class Out(BaseModel):
    ok: bool
    label: str
    n: int

class Batch(BaseModel):
    items: list[Out]

class W(Task):
    """Work one item."""
    v: str
    returns: Out

class Scan(Task):
    """Scan for a batch."""
    returns: Batch

'''


def lint(body: str, ignore: tuple[str, ...] = ("AS033",)):
    """Lint HEADER + body. AS033 (unused task) is ignored by default because
    the shared HEADER's helper tasks are unused in most snippets; the AS033
    tests use self-contained sources instead."""
    diags = lint_module(parse_source(HEADER + body))
    return [d for d in diags if d.code not in ignore]


def codes(diags):
    return {d.code for d in diags}


def by_code(diags, code):
    return [d for d in diags if d.code == code]


def test_clean_baseline():
    diags = lint('''class R(Task):
    """Route."""
    v: str
    returns: Out
    a = W(v=v)
    b = W(v=a.label) if a.ok else None
''')
    assert diags == []


def test_as003_unknown_attribute():
    diags = lint('''class T(Task):
    """Do."""
    returns: Out
    retries = 3
''')
    assert codes(diags) == {"AS003"}


def test_as004_missing_docstring():
    diags = lint("class T(Task):\n    returns: Out\n")
    assert codes(diags) == {"AS004"}


def test_as005_anonymous_and_unknown_returns():
    assert codes(lint('class T(Task):\n    """D."""\n    returns: str\n')) == {"AS005"}
    assert codes(lint('class T(Task):\n    """D."""\n    v: str\n')) == {"AS005"}
    assert codes(lint('class T(Task):\n    """D."""\n    returns: Nope\n')) == {"AS005"}


def test_as006_filter_outside_the_cage():
    diags = lint('''class R(Task):
    """Route."""
    returns: Out
    scan = Scan()
    work = [W(v=i.label) for i in scan.items if i.label.startswith("x")]
''')
    assert "AS006" in codes(diags)


def test_as007_undefined_task():
    diags = lint('''class R(Task):
    """Route."""
    returns: Out
    step = Missing()
''')
    assert codes(diags) == {"AS007"}


def test_as008_reference_before_bound_and_unknown():
    diags = lint('''class R(Task):
    """Route."""
    returns: Out
    a = W(v=b.label)
    b = W(v="x")
''')
    assert "AS008" in codes(diags)
    diags = lint('''class R(Task):
    """Route."""
    returns: Out
    a = W(v=nowhere.label)
''')
    assert codes(diags) == {"AS008"}


def test_as009_self_reference_cycle():
    diags = lint('''class R(Task):
    """Route."""
    returns: Out
    a = W(v=a.label)
''')
    assert "AS009" in codes(diags)
    assert "a -> a" in by_code(diags, "AS009")[0].message


def test_as010_retry_without_then():
    diags = lint('''class T(Task):
    """D."""
    returns: Out
    on_failure = Retry(max=2, backoff_s=5)
''')
    assert codes(diags) == {"AS010"}
    # a nested chain must terminate too
    diags = lint('''class T(Task):
    """D."""
    returns: Out
    on_failure = Escalate(channel="pager", timeout_s=60,
                          then=Retry(max=1, backoff_s=1))
''')
    assert codes(diags) == {"AS010"}


def test_as011_gates():
    # not a path at all (parser also flags the non-None else as P010)
    diags = lint('''class R(Task):
    """Route."""
    returns: Out
    a = W(v="x")
    b = W(v="y") if a.n > 0 else None
''')
    assert "AS011" in codes(diags)
    # a path, but not boolean
    diags = lint('''class R(Task):
    """Route."""
    returns: Out
    a = W(v="x")
    b = W(v="y") if a.label else None
''')
    assert codes(diags) == {"AS011"}
    # rooted at an input, not a prior step
    diags = lint('''class R(Task):
    """Route."""
    go: str
    returns: Out
    b = W(v="y") if go else None
''')
    assert codes(diags) == {"AS011"}


def test_as020_contract_mismatches():
    body = '''class R(Task):
    """Route."""
    returns: Out
    a = W(bogus="x")
'''
    messages = [d.message for d in by_code(lint(body), "AS020")]
    # both findings: the unknown kwarg AND the required input it fails to supply
    assert any("no input 'bogus'" in m for m in messages)
    assert any("does not supply input(s) v" in m for m in messages)

    body = '''class R(Task):
    """Route."""
    returns: Out
    a = W()
'''
    assert "does not supply input(s) v" in by_code(lint(body), "AS020")[0].message

    body = '''class R(Task):
    """Route."""
    returns: Out
    a = W(v="x")
    b = W(v=a.n)
'''
    assert "does not match input 'v'" in by_code(lint(body), "AS020")[0].message


def test_as020_literal_outside_enum():
    diags = lint('''class P(Task):
    """Pick a tier."""
    tier: Enum["a", "b"]
    returns: Out

class R(Task):
    """Route."""
    returns: Out
    p = P(tier="z")
''')
    assert codes(diags) == {"AS020"}


def test_as021_list_scalar_mismatches():
    # a fan-out's collected results into a scalar input
    diags = lint('''class R(Task):
    """Route."""
    returns: Out
    scan = Scan()
    many = [W(v=i.label) for i in scan.items]
    bad  = W(v=many.label)
''')
    assert "AS021" in codes(diags)
    # fanning out over a scalar
    diags = lint('''class R(Task):
    """Route."""
    returns: Out
    a = W(v="x")
    many = [W(v=i.label) for i in a.label]
''')
    assert "AS021" in codes(diags)


def test_as022_missing_field_and_closed_env():
    diags = lint('''class R(Task):
    """Route."""
    returns: Out
    a = W(v="x")
    b = W(v=a.nope)
''')
    assert codes(diags) == {"AS022"}
    diags = lint('''class R(Task):
    """Route."""
    returns: Out
    a = W(v=env.nope)
''')
    assert codes(diags) == {"AS022"}
    # the closed namespace itself is fine
    assert (
        lint('''class R(Task):
    """Route."""
    returns: Out
    a = W(v=env.now)
''')
        == []
    )


def test_as030_must_rule_without_why():
    diags = lint('''class T(Task):
    """D."""
    returns: Out
    constraints = ["Be careful"]
''')
    assert codes(diags) == {"AS030"}
    assert "Be careful" in diags[0].message


def test_as031_constraint_ceiling():
    rules = ", ".join(f'("rule {i}", "why {i}")' for i in range(16))
    diags = lint(f'''class T(Task):
    """D."""
    returns: Out
    constraints = [{rules}]
''')
    assert codes(diags) == {"AS031"}


def test_as033_unused_task():
    diags = lint_module(
        parse_source('''"""D."""
from pydantic import BaseModel
from agentspec import Task

class Out(BaseModel):
    ok: bool

class Used(Task):
    """Used."""
    returns: Out

class Orphan(Task):
    """Never referenced."""
    returns: Out

class R(Task):
    """Route."""
    returns: Out
    a = Used()
''')
    )
    assert codes(diags) == {"AS033"}
    assert "Orphan" in diags[0].message


def test_as033_skips_worker_libraries():
    # no orchestrator at all: a library of tasks, nothing to reach from
    diags = lint_module(
        parse_source('''"""D."""
from pydantic import BaseModel
from agentspec import Task

class Out(BaseModel):
    ok: bool

class A(Task):
    """A."""
    returns: Out

class B(Task):
    """B."""
    returns: Out
''')
    )
    assert diags == []


def test_as034_exclusive_tool_without_undo():
    diags = lint('''class T(Task):
    """Mutate."""
    returns: Out
    tools = [Tool("git", ops=["push"], exclusive=True)]
''')
    assert codes(diags) == {"AS034"}


def test_as035_fanout_without_item_failure():
    diags = lint('''class R(Task):
    """Route."""
    returns: Out
    scan = Scan()
    work = [W(v=i.label) for i in scan.items]
''')
    assert codes(diags) == {"AS035"}


def test_p014_duplicate_definition():
    diags = lint('''class T(Task):
    """D."""
    returns: Out

class T(Task):
    """D again."""
    returns: Out
''')
    assert "P014" in codes(diags)
