"""
Shared runtime path resolution for both local/Docker execution and read-only serverless
platforms (such as Vercel Python Functions where only /tmp is writable).
"""

import os
import tempfile
from pathlib import Path


def is_readonly_environment(root: Path) -> bool:
    if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return True
    probe = root / "data"
    try:
        probe.mkdir(parents=True, exist_ok=True)
        return not os.access(probe, os.W_OK)
    except OSError:
        return True


def _serverless_base_dir() -> Path:
    base = Path(tempfile.gettempdir()) / "cyberpro"
    base.mkdir(parents=True, exist_ok=True)
    return base


def resolve_db_path(root: Path) -> Path:
    env_db = os.environ.get("CYBERPRO_DB", "").strip()
    if env_db:
        raw = Path(env_db)
        resolved = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    if is_readonly_environment(root):
        db_file = _serverless_base_dir() / "cyberpro.db"
        db_file.parent.mkdir(parents=True, exist_ok=True)
        return db_file

    db_file = (root / "data" / "cyberpro.db").resolve()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    return db_file


def resolve_writable_dir(root: Path, subpath: str) -> Path:
    if is_readonly_environment(root):
        target = _serverless_base_dir() / subpath
    else:
        target = root / subpath
    target.mkdir(parents=True, exist_ok=True)
    return target


def resolve_data_read_path(root: Path, filename: str) -> Path:
    writable_candidate = resolve_writable_dir(root, "data") / filename
    if writable_candidate.is_file():
        return writable_candidate
    return root / "data" / filename
