"""Concrete model adapters. The core never imports a provider SDK: an
adapter is any `Callable[[prompt], reply]`, and this module only offers a
provider-independent subprocess bridge (`claude -p`, `ollama run …`, or
any command that reads a prompt on stdin and prints the reply)."""

import subprocess

from agentspec.eval.runner import Adapter, EvalError


def subprocess_adapter(command: list[str], *, timeout_s: float = 600) -> Adapter:
    def run(prompt: str) -> str:
        try:
            result = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except FileNotFoundError as exc:
            raise EvalError(f"adapter command not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise EvalError(f"adapter timed out after {timeout_s}s") from exc
        if result.returncode != 0:
            raise EvalError(f"adapter exited {result.returncode}: {result.stderr.strip()[:500]}")
        return result.stdout

    return run
