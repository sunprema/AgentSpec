"""The lint rule catalog: code → (summary, default severity).

Structural problems (the file cannot be fully represented as a SpecModel)
are reported by the parser as P0xx and folded into lint output:
P002 covers forbidden module-level statements (AS001/AS002 territory),
P009 forbidden statements in class bodies, P014 duplicate definitions (AS032).
"""

from agentspec.diagnostics import Diagnostic
from agentspec.parser import SourceLoc

CATALOG: dict[str, tuple[str, str]] = {
    "AS003": ("unknown attribute on a task", "error"),
    "AS004": ("task has no docstring", "error"),
    "AS005": ("task output must be a named schema", "error"),
    "AS006": ("fan-out filter outside the comprehension cage", "error"),
    "AS007": ("reference to an undefined task", "error"),
    "AS008": ("reference to a name that is not bound at this point", "error"),
    "AS009": ("dependency cycle in the pipeline", "error"),
    "AS010": ("Retry/Escalate without a terminating then", "error"),
    "AS011": ("gate must be a boolean field of a prior step", "error"),
    "AS020": ("input/output contract mismatch", "error"),
    "AS021": ("list/scalar mismatch across a bind", "error"),
    "AS022": ("attribute path does not exist", "error"),
    "AS030": ("must-rule without a why", "warning"),
    "AS031": ("task exceeds ~15 constraints", "warning"),
    "AS033": ("unused task", "warning"),
    "AS034": ("mutating task without undo", "warning"),
    "AS035": ("fan-out task without on_item_failure", "warning"),
    "AS036": ("duplicate rule id within a task", "warning"),
    "AS037": ("rule id is not kebab-case", "warning"),
    "AS038": ("fallback literal violates the output contract", "error"),
    "AS039": ("ambiguous root: multiple orchestrator roots", "warning"),
    "AS040": ("on_item_failure on a task nothing fans out over", "warning"),
    "AS041": ("reduction mapping produces a value outside the enum", "warning"),
    "AS042": ("enum value unreachable by mapping or on_uncertain", "warning"),
    "AS043": ("derivation without a True: catch-all row", "error"),
    "AS044": ("derivation rows with inconsistent field sets", "error"),
    "AS045": ("task declares no on_failure", "warning"),
    "AS046": ("step weakens an inherited must-rule", "error"),
}


def mk(code: str, message: str, loc: SourceLoc) -> Diagnostic:
    _summary, severity = CATALOG[code]
    return Diagnostic(
        code=code,
        message=message,
        file=loc.file,
        line=loc.line,
        col=loc.col,
        severity=severity,  # type: ignore[arg-type]
    )
