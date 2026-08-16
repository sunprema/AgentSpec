"""The `aspec` command line: lint | plan | graph | fmt | diff | eval | run | studio."""

import argparse
import json
import sys

from agentspec import __version__

SUBCOMMANDS = {
    "lint": "Check a spec for conformance, type-flow, and rule-hygiene problems",
    "plan": "Show the derived execution waves: concurrency, gates, fan-out",
    "graph": "Render the pipeline as a Mermaid flowchart",
    "fmt": "Format a spec file canonically",
    "diff": "Semantic diff of two specs: capability widening flagged loudly",
    "eval": "Run fixture-based behavioral tests against a spec",
    "run": "Guarded execution of a spec (outputs validated against contracts)",
    "studio": "Live spec workbench in the browser: canvas, inspector, gate simulator",
    "lsp": "Language server over stdio (diagnostics in your editor)",
    "new": "Scaffold a starter spec: worker, gated orchestrator, rules, fallbacks",
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
        if name == "eval":
            sub.add_argument("eval_file", nargs="+", help="path to an .eval.toml file")
            sub.add_argument(
                "--adapter-cmd",
                help="shell command that reads a prompt on stdin and prints "
                "the model reply, e.g. 'claude -p'",
            )
            sub.add_argument("--json", action="store_true", help="emit results as JSON")
            continue
        if name == "lsp":
            continue  # no arguments: the editor speaks LSP over stdio
        if name == "new":
            sub.add_argument("name", help="spec name or path (.aspec.py appended if missing)")
            continue
        if name == "studio":
            sub.add_argument("spec", help="path to a .aspec.py file")
            sub.add_argument("--port", type=int, default=7788, help="port (default 7788)")
            sub.add_argument(
                "--no-open", action="store_true", help="do not open the browser automatically"
            )
            sub.add_argument(
                "--trace",
                help="overlay an `aspec run --json` result on the canvas (watched like the spec)",
            )
            sub.add_argument(
                "--export",
                metavar="FILE",
                help="write the studio view as one self-contained HTML file and exit (no server)",
            )
            continue
        if name == "diff":
            sub.add_argument("old", help="path to the base .aspec.py file")
            sub.add_argument("new", help="path to the changed .aspec.py file")
            sub.add_argument("--json", action="store_true", help="emit changes as JSON")
            continue
        if name == "run":
            sub.add_argument("spec", help="path to a .aspec.py file")
            sub.add_argument("--task", help="task to run (default: the root task)")
            sub.add_argument("--context", default="", help="freeform dispatch context")
            sub.add_argument(
                "--input",
                action="append",
                default=[],
                metavar="NAME=VALUE",
                help="a task input (VALUE parsed as JSON when possible); repeatable",
            )
            sub.add_argument(
                "--adapter-cmd",
                help="shell command that reads a prompt on stdin and prints "
                "the model reply, e.g. 'claude -p'",
            )
            sub.add_argument(
                "--max-repairs",
                type=int,
                default=2,
                help="bounded-repair attempts per guarded call (default 2)",
            )
            sub.add_argument(
                "--orchestrate",
                action="store_true",
                help="one guarded subagent per step instead of a single agent",
            )
            sub.add_argument(
                "--dev",
                action="store_true",
                help="development dispatch: the routine may ask you ONE "
                "clarifying question at declared doubt points; every question "
                "is reported as a spec gap. Production is always unattended.",
            )
            sub.add_argument("--json", action="store_true", help="emit the result as JSON")
            continue
        sub.add_argument("spec", nargs="+", help="path to a .aspec.py file")
        if name == "lint":
            sub.add_argument("--json", action="store_true", help="emit diagnostics as JSON")
            sub.add_argument("--strict", action="store_true", help="exit nonzero on warnings (CI)")
        elif name == "plan":
            sub.add_argument("--json", action="store_true", help="emit the plan as JSON")
        elif name == "graph":
            sub.add_argument("--failures", action="store_true", help="include the failure layer")
            sub.add_argument("--out", help="write the markdown to this file")
        elif name == "fmt":
            sub.add_argument(
                "--check",
                action="store_true",
                help="report files that would change, without writing (CI)",
            )
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
    if args.command == "fmt":
        return cmd_fmt(args)
    if args.command == "diff":
        return cmd_diff(args)
    if args.command == "eval":
        return cmd_eval(args)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "studio":
        return cmd_studio(args)
    if args.command == "lsp":
        from agentspec.lsp import serve_stdio

        return serve_stdio()
    if args.command == "new":
        return cmd_new(args)
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


def cmd_fmt(args: argparse.Namespace) -> int:
    from pathlib import Path

    from agentspec.fmt import FormatError, format_source

    changed = []
    for spec in args.spec:
        path = Path(spec)
        try:
            source = path.read_text()
            formatted = format_source(source, spec)
        except (OSError, FormatError) as exc:
            print(f"aspec fmt: {exc}", file=sys.stderr)
            return 1
        if formatted == source:
            continue
        changed.append(spec)
        if args.check:
            print(f"would reformat {spec}")
        else:
            path.write_text(formatted)
            print(f"reformatted {spec}")
    total = len(args.spec)
    if not changed:
        print(f"{total} file(s) already formatted")
        return 0
    print(f"{len(changed)} of {total} file(s) {'need formatting' if args.check else 'reformatted'}")
    return 1 if args.check else 0


def cmd_diff(args: argparse.Namespace) -> int:
    from agentspec.diff import diff_modules, render_text
    from agentspec.parser import parse_file

    modules = []
    for path in (args.old, args.new):
        try:
            # one cache per side: old and new may be checkouts of the same
            # sibling files, and must not resolve into each other
            module = parse_file(path, cache={})
        except OSError as exc:
            print(f"aspec diff: {exc}", file=sys.stderr)
            return 2
        if module.errors:
            for diag in module.errors:
                print(diag.render(), file=sys.stderr)
            print(
                f"aspec diff: {path} has parse errors; run `aspec lint` and fix them first",
                file=sys.stderr,
            )
            return 2
        modules.append(module)
    changes = diff_modules(modules[0], modules[1])
    if args.json:
        print(json.dumps([c.model_dump() for c in changes], indent=2, default=str))
    else:
        print(render_text(changes, args.old, args.new))
    # diff(1) convention: 0 same, 1 different, 2 trouble
    return 1 if changes else 0


def cmd_eval(args: argparse.Namespace) -> int:
    import shlex

    from agentspec.eval import EvalError, render_report, run_eval, subprocess_adapter

    if not args.adapter_cmd:
        print(
            "aspec eval: an adapter is required — e.g. --adapter-cmd 'claude -p' "
            "(any command that reads a prompt on stdin and prints the reply)",
            file=sys.stderr,
        )
        return 2
    adapter = subprocess_adapter(shlex.split(args.adapter_cmd))
    reports = []
    for eval_file in args.eval_file:
        try:
            reports.append(run_eval(eval_file, adapter))
        except (OSError, EvalError) as exc:
            print(f"aspec eval: {exc}", file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps([r.model_dump() for r in reports], indent=2))
    else:
        print("\n\n".join(render_report(r) for r in reports))
    return 0 if all(r.passed for r in reports) else 1


def cmd_run(args: argparse.Namespace) -> int:
    import shlex

    from agentspec.eval import EvalError, subprocess_adapter
    from agentspec.run import RunError, orchestrate, run_routine

    if not args.adapter_cmd:
        print(
            "aspec run: an adapter is required — e.g. --adapter-cmd 'claude -p' "
            "(any command that reads a prompt on stdin and prints the reply)",
            file=sys.stderr,
        )
        return 2
    inputs: dict = {}
    for pair in args.input:
        name, sep, value = pair.partition("=")
        if not sep:
            print(f"aspec run: --input expects NAME=VALUE, got '{pair}'", file=sys.stderr)
            return 2
        try:
            inputs[name] = json.loads(value)
        except json.JSONDecodeError:
            inputs[name] = value
    adapter = subprocess_adapter(shlex.split(args.adapter_cmd))
    runner = orchestrate if args.orchestrate else run_routine
    try:
        result = runner(
            args.spec,
            adapter,
            context=args.context,
            inputs=inputs,
            task_name=args.task,
            max_repairs=args.max_repairs,
            ask=_terminal_ask if args.dev else None,
        )
    except (OSError, RunError, EvalError) as exc:
        print(f"aspec run: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result.model_dump(), indent=2, default=str))
    else:
        print(
            f"{result.task} [{result.mode}] — {result.status} "
            f"({result.adapter_calls} adapter call(s))"
        )
        for step in result.steps:
            print(f"  {step.status:18} {step.var} = {step.task}")
        if result.report:
            print(result.report)
        for sub in result.substitutions:
            reason = f" ({sub.reason})" if sub.reason else ""
            print(f"  substitution: [{sub.task}] {sub.tool} -> {sub.used}{reason}")
        for conflict in result.rule_conflicts:
            print(
                f"  rule conflict: [{conflict.task}] "
                f"{' vs '.join(conflict.rules)} -> {conflict.resolution}"
            )
        for note in result.notes:
            print(f"  note: {note}")
        if result.output is not None:
            print(json.dumps(result.output, indent=2, default=str))
    if args.dev and not args.json:
        _print_gap_report(result)
    return 0 if result.status != "aborted" else 1


def _terminal_ask(question: str) -> str | None:
    print(f"\n[dev] the routine asks: {question}")
    try:
        answer = input("[dev] your answer (empty to decline): ").strip()
    except EOFError:
        return None
    return answer or None


def _print_gap_report(result) -> None:
    if not result.clarifications:
        print(
            "\ndev mode: no clarifications were needed — this routine may "
            "be ready for unattended dispatch"
        )
        return
    count = len(result.clarifications)
    print(
        f"\ndev mode: {count} clarification(s) — each is a spec gap; "
        "consider a rule (with a why and a since=):"
    )
    for index, item in enumerate(result.clarifications, 1):
        answer = (
            item.answer if item.answer is not None else "(declined — declared fallback applied)"
        )
        print(f"  {index}. [{item.task}] Q: {item.question}")
        print(f"     A: {answer}")


def cmd_new(args: argparse.Namespace) -> int:
    from pathlib import Path

    from agentspec.cli.template import render_template
    from agentspec.fmt import FormatError, format_source

    path = Path(args.name if args.name.endswith(".aspec.py") else f"{args.name}.aspec.py")
    if path.exists():
        print(f"aspec new: {path} already exists — refusing to overwrite", file=sys.stderr)
        return 1
    source = render_template(path.name.removesuffix(".aspec.py"))
    try:
        source = format_source(source, str(path))
    except FormatError as exc:  # template drift — still write the raw form
        print(f"aspec new: template formatting failed ({exc}); writing unformatted")
    path.write_text(source)
    print(f"created {path}")
    print(f"next: aspec lint {path}  ·  aspec studio {path}  ·  aspec graph {path}")
    return 0


def cmd_studio(args: argparse.Namespace) -> int:
    from pathlib import Path

    from agentspec.studio import export_html, serve

    if not Path(args.spec).exists():
        print(f"aspec studio: {args.spec}: no such file", file=sys.stderr)
        return 1
    if args.export:
        try:
            html = export_html(args.spec)
        except ValueError as exc:
            print(f"aspec studio: {exc}", file=sys.stderr)
            return 1
        Path(args.export).write_text(html)
        print(f"wrote {args.export} (self-contained; simulator included)")
        return 0
    return serve(args.spec, port=args.port, open_browser=not args.no_open, trace_path=args.trace)


if __name__ == "__main__":
    sys.exit(main())
