"""LabRat: `from labrat_cli import Lab` for scripts; `uv run labrat` for the terminal."""
from .lab import Lab, LabError

__all__ = ["Lab", "LabError"]
