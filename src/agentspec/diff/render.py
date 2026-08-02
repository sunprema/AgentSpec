"""Render a change list for review: grouped by object, widening first."""

from collections import Counter

from agentspec.diff.model import Change

_ORDER = ["widens", "reshapes", "narrows", "neutral"]


def render_text(changes: list[Change], old_path: str, new_path: str) -> str:
    if not changes:
        return f"no semantic differences: {old_path} -> {new_path}"

    lines = [f"semantic diff: {old_path} -> {new_path}", ""]

    grouped: dict[str, list[Change]] = {}
    for change in changes:
        grouped.setdefault(change.owner, []).append(change)
    owners = sorted(grouped, key=lambda o: (o != "<module>", o))

    for owner in owners:
        lines.append(f"{owner}:")
        ordered = sorted(grouped[owner], key=lambda c: _ORDER.index(c.direction))
        for change in ordered:
            tag = f"[{change.direction}]"
            lines.append(f"  {tag:<11}{change.detail}")
        lines.append("")

    counts = Counter(c.direction for c in changes)
    summary = f"{len(changes)} change(s): " + ", ".join(
        f"{counts[d]} {d}" for d in _ORDER if counts[d]
    )
    lines.append(summary)
    if counts["widens"]:
        lines.append(
            f"!! {counts['widens']} widening change(s) — more capability or less "
            "safety; review these first"
        )
    return "\n".join(lines)
