"""`aspec diff`: semantic comparison of two specs — SpecModels, not texts."""

from agentspec.diff.compare import diff_modules
from agentspec.diff.model import Change
from agentspec.diff.render import render_text

__all__ = ["Change", "diff_modules", "render_text"]
