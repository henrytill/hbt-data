"""Parsing YAML, quickly.

PyYAML's pure-Python loader is the bottleneck in a full run -- it costs more
than all of the subprocesses put together for the faster implementations -- and
libyaml's ``CSafeLoader`` parses the same safe schema seven times faster.  It
is not guaranteed to be present, so this falls back.
"""

from __future__ import annotations

from typing import Any

import yaml

try:  # pragma: no cover - depends on how PyYAML was built
    from yaml import CSafeLoader as SafeLoader
except ImportError:  # pragma: no cover
    from yaml import SafeLoader  # type: ignore[assignment]


def load_yaml(text: str) -> Any:
    """Parse one YAML document under the safe schema."""
    return yaml.load(text, Loader=SafeLoader)
