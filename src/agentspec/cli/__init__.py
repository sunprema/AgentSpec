"""The `aspec` command line: lint | plan | graph | fmt | eval | run."""

import argparse
import json
import sys

from agentspec import __version__

SUBCOMMANDS = {
    "lint": "Check a spec for conformance, type-flow, and rule-hygiene problems",
    "plan": "Show the derived execution waves: concurrency, gates, fan-out",
    "graph": "Render the pipeline as a Mermaid flowchart",
    "fmt": "Format a spec file canonically",
    "eval": "Run fixture-based behavioral tests against a spec",
    "run": "Guarded execution of a spec (outputs validated against contracts)",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aspec",
        description="Static toolchain for AgentSpec specifications (.aspec.py).",
    )
    parser.add_argument("--version", action="version", version=f"aspec {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    for name, help_text in SUBCOMMANDS.items():
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("spec", nargs="+", help="path to a .aspec.py file")
        if name == "lint":
            sub.add_argument("--json", action="store_true", help="emit diagnostics as JSON")
            sub.add_argument("--strict", action="store_true", help="exit nonzero on warnings (CI)")
        elif name == "plan":
            sub.add_argument("--json", action="store_true", help="emit the plan as JSON")
        elif name == "graph":
            sub.add_argument("--failures", action="store_true", help="include the failure layer")
            sub.add_argument("--out", help="write the markdown to this file")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "lint":
        return cmd_lint(args)
    if args.command == "plan":
        return cmd_plan(args)
    if args.command == "graph":
        return cmd_graph(args)
    print(f"aspec {args.command}: not implemented yet", file=sys.stderr)
    return 2


def cmd_lint(args: argparse.Namespace) -> int:
    from agentspec.lint import dedupe, lint_module
    from agentspec.parser import parse_file

    cache: dict = {}
    diagnostics = []
    for spec in args.spec:
        try:
            module = parse_file(spec, cache=cache)
        except OSError as exc:
            print(f"aspec lint: {exc}", file=sys.stderr)
            return 1
        diagnostics.extend(lint_module(module))
    diagnostics = dedupe(diagnostics)

    errors = [d for d in diagnostics if d.severity == "error"]
    warnings = [d for d in diagnostics if d.severity == "warning"]
    if args.json:
        print(json.dumps([d.model_dump() for d in diagnostics], indent=2))
    else:
        for diag in diagnostics:
            print(diag.render())
        if diagnostics:
            print(f"{len(errors)} error(s), {len(warnings)} warning(s)")
        else:
            print("clean")
    if errors:
        return 1
    if warnings and args.strict:
        return 2
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    from agentspec.parser import parse_file
    from agentspec.plan import build_plans, render_text

    cache: dict = {}
    plans = []
    for spec in args.spec:
        try:
            module = parse_file(spec, cache=cache)
        except OSError as exc:
            print(f"aspec plan: {exc}", file=sys.stderr)
            return 1
        if module.errors:
            for diag in module.errors:
                print(diag.render(), file=sys.stderr)
            print(
                f"aspec plan: {spec} has parse errors; run `aspec lint` and fix them first",
                file=sys.stderr,
            )
            return 1
        module_plans = build_plans(module)
        if not module_plans and not args.json:
            print(f"{spec}: no orchestrator — nothing to plan")
        plans.extend(module_plans)
    if args.json:
        print(json.dumps([p.model_dump() for p in plans], indent=2))
    else:
        print("\n\n".join(render_text(p) for p in plans))
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    from pathlib import Path

    from agentspec.graph import render_markdown
    from agentspec.parser import parse_file

    cache: dict = {}
    documents = []
    for spec in args.spec:
        try:
            module = parse_file(spec, cache=cache)
        except OSError as exc:
            print(f"aspec graph: {exc}", file=sys.stderr)
            return 1
        if module.errors:
            for diag in module.errors:
                print(diag.render(), file=sys.stderr)
            print(
                f"aspec graph: {spec} has parse errors; run `aspec lint` and fix them first",
                file=sys.stderr,
            )
            return 1
        documents.append(render_markdown(module, failures=args.failures))
    output = "\n".join(documents)
    if args.out:
        Path(args.out).write_text(output)
        print(f"wrote {args.out}")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
