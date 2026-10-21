"""配置加载与合并。

优先级：默认配置(default_config.toml) < secrets.toml < 宿主传入 dict
"""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).parent / "config" / "default_config.toml"
SECRETS_NAME = "secrets.toml"


def _default_content_root() -> Path:
    """Use the monorepo content/ directory, with a portable standalone fallback."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "src" / "echo_reader").is_dir() and (parent / "components" / "medialab").is_dir():
            return parent / "content"
    return Path.cwd() / "content"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def load_config(
    config_path: str | Path | None = None,
    secrets_path: str | Path | None = None,
    overrides: dict | None = None,
) -> dict:
    cfg = _read_toml(DEFAULT_CONFIG_PATH)

    # 资料库根目录优先感知 LIBRARY_ROOT 环境变量（echo 展示端约定）；
    # 宿主显式配置（config.toml / overrides）仍可覆盖
    env_root = os.environ.get("LIBRARY_ROOT", "").strip()
    if env_root:
        cfg["paths"]["delivery"] = env_root

    if config_path and Path(config_path).is_file():
        cfg = _deep_merge(cfg, _read_toml(Path(config_path)))

    secrets = _load_secrets(secrets_path)
    if secrets:
        cfg = _deep_merge(cfg, secrets)

    if overrides:
        cfg = _deep_merge(cfg, overrides)
    if not str(cfg.get("paths", {}).get("delivery") or "").strip():
        cfg["paths"]["delivery"] = str(_default_content_root())
    return cfg


def _load_secrets(secrets_path: str | Path | None = None) -> dict:
    if secrets_path is not None:
        explicit = Path(secrets_path)
        return _read_toml(explicit) if explicit.is_file() else {}
    candidates: list[Path] = []
    candidates.append(Path.cwd() / "config" / SECRETS_NAME)
    candidates.append(Path.cwd() / SECRETS_NAME)
    candidates.append(Path(__file__).parent / "config" / SECRETS_NAME)
    for p in candidates:
        if p.is_file():
            return _read_toml(p)
    return {}


def resolve_dir(cfg: dict, key: str) -> Path:
    """把相对路径解析为绝对路径（相对当前工作目录）。"""
    p = Path(os.path.expanduser(str(cfg.get(key, ""))))
    return p if p.is_absolute() else Path.cwd() / p
