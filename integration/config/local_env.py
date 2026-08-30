"""Load optional local environment files without mixing module namespaces.

The repository intentionally keeps A, B and D configuration in separate files:

* ``.env``               -> RIA / A settings (loaded by pydantic-settings)
* ``codearts.env``       -> B CodeArts CLI settings
* ``tracecoder_llm.env`` -> D TraceCoder settings

This helper only loads the requested local file into ``os.environ``. Missing
files and missing python-dotenv remain non-fatal so offline CI keeps working.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


REPO_ROOT = Path(__file__).resolve().parents[2]


def load_local_env(filename: str, *, override: bool = False) -> bool:
    """Load one ignored, repository-local env file if it exists.

    Returns ``True`` when python-dotenv loaded the file and ``False`` when the
    file is absent or the optional dependency is unavailable.
    """

    path = REPO_ROOT / filename
    if not path.is_file():
        return False
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return False
    return bool(load_dotenv(dotenv_path=path, override=override))


def load_codearts_env(*, override: bool = False) -> bool:
    """Load the local B/CodeArts configuration file."""

    return load_local_env("codearts.env", override=override)


@contextmanager
def temporary_local_env(*filenames: str) -> Iterator[None]:
    """Load ignored env files for one explicit run, then restore the process.

    Provider helpers are imported by the regular test suite.  A live run may
    need local credentials, but those values must not leak into later tests or
    unrelated adapters in the same Python process.
    """
    previous = dict(os.environ)
    try:
        for filename in filenames:
            load_local_env(filename)
        yield
    finally:
        current_keys = set(os.environ)
        previous_keys = set(previous)
        for key in current_keys - previous_keys:
            os.environ.pop(key, None)
        for key in previous_keys:
            if os.environ.get(key) != previous[key]:
                os.environ[key] = previous[key]


__all__ = ["REPO_ROOT", "load_codearts_env", "load_local_env", "temporary_local_env"]
