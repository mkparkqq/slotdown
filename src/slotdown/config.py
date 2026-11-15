"""Settings loaded from `slotdown.toml` or `[tool.slotdown]` in `pyproject.toml`."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    # Jinja whitespace control: drop the newline after a block tag and strip
    # indentation before it, so `{% for %}` lines don't leave blank lines behind.
    trim_blocks: bool = True
    lstrip_blocks: bool = True
    # Collapse runs of 3+ newlines in the output into a single blank line.
    collapse_blank_lines: bool = True
    # Make `slotdown check` fail on warnings (e.g. unused fields).
    warnings_as_errors: bool = False


def load_config(path: Path | None = None, start: Path | None = None) -> Config:
    """Load an explicit config file, or search upward from `start` (default: cwd)."""
    if path is not None:
        return _from_file(path)
    directory = (start or Path.cwd()).resolve()
    for candidate_dir in (directory, *directory.parents):
        dedicated = candidate_dir / "slotdown.toml"
        if dedicated.is_file():
            return _from_file(dedicated)
        pyproject = candidate_dir / "pyproject.toml"
        if pyproject.is_file() and "slotdown" in _read(pyproject).get("tool", {}):
            return _from_file(pyproject)
    return Config()


def _from_file(path: Path) -> Config:
    data = _read(path)
    if path.name == "pyproject.toml":
        data = data.get("tool", {}).get("slotdown", {})
    known = {f.name for f in fields(Config)}
    unknown = data.keys() - known
    if unknown:
        raise ConfigError(f"{path}: unknown setting(s): {', '.join(sorted(unknown))}")
    for key, value in data.items():
        if not isinstance(value, bool):
            raise ConfigError(f"{path}: '{key}' must be true or false")
    return Config(**data)


def _read(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
