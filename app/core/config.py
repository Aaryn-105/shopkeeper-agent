"""OmegaConf layered config loader."""
from __future__ import annotations
from pathlib import Path
from functools import lru_cache
from omegaconf import OmegaConf


CONF_DIR = Path(__file__).resolve().parent.parent.parent / "conf"


@lru_cache(maxsize=1)
def get_cfg():
    """Load default + env-specific config; env defaults to local."""
    import os
    env = os.environ.get("APP_ENV", "local")
    base = OmegaConf.load(CONF_DIR / "default.yaml")
    env_file = CONF_DIR / f"{env}.yaml"
    if env_file.exists():
        overlay = OmegaConf.load(env_file)
        base = OmegaConf.merge(base, overlay)
    base.app.env = env
    return base


cfg = get_cfg()