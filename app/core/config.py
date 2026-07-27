"""OmegaConf layered config loader with .env overlay (stdlib, no deps).

Order of precedence (highest wins):
  1. process environment
  2. .env file at repo root
  3. conf/<APP_ENV>.yaml
  4. conf/default.yaml
"""
from __future__ import annotations
import os
from functools import lru_cache
from pathlib import Path
from omegaconf import OmegaConf


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
CONF_DIR = ROOT_DIR / "conf"


def _load_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env parser: KEY=value lines, # comments, blank lines ignored."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def get_cfg():
    env = os.environ.get("APP_ENV", "local")
    base = OmegaConf.load(CONF_DIR / "default.yaml")
    env_yaml = CONF_DIR / f"{env}.yaml"
    if env_yaml.exists():
        base = OmegaConf.merge(base, OmegaConf.load(env_yaml))

    # .env file does not override existing process env (12-factor friendly).
    dotenv_map = _load_dotenv(ROOT_DIR / ".env")
    merged_env = {**dotenv_map, **os.environ}

    def env_or(k: str, default):
        return merged_env.get(k, default)

    base.mysql.host = env_or("MYSQL_HOST", base.mysql.host)
    base.mysql.port = int(env_or("MYSQL_PORT", base.mysql.port))
    base.mysql.meta_db = env_or("MYSQL_META_DB", base.mysql.meta_db)
    base.mysql.dw_db = env_or("MYSQL_DW_DB", base.mysql.dw_db)
    base.mysql.pool_size = int(env_or("MYSQL_POOL_SIZE", base.mysql.pool_size))
    base.mysql.pool_recycle = int(env_or("MYSQL_POOL_RECYCLE", base.mysql.pool_recycle))
    base.mysql.admin_user = env_or("MYSQL_ADMIN_USER", getattr(base.mysql, "admin_user", "root"))
    base.mysql.admin_password = env_or("MYSQL_ADMIN_PASSWORD", getattr(base.mysql, "admin_password", ""))
    base.mysql.ro_user = env_or("MYSQL_RO_USER", getattr(base.mysql, "ro_user", "readonly"))
    base.mysql.ro_password = env_or("MYSQL_RO_PASSWORD", getattr(base.mysql, "ro_password", ""))

    base.embedding.model_path = env_or("BGE_MODEL_PATH", base.embedding.model_path)
    base.embedding.dim = int(env_or("EMBEDDING_DIM", base.embedding.dim))
    base.embedding.batch_size = int(env_or("EMBEDDING_BATCH_SIZE", base.embedding.batch_size))

    base.faiss.index_dir = env_or("FAISS_INDEX_DIR", base.faiss.index_dir)
    base.faiss.top_k_column = int(env_or("FAISS_TOP_K_COLUMN", base.faiss.top_k_column))
    base.faiss.top_k_metric = int(env_or("FAISS_TOP_K_METRIC", base.faiss.top_k_metric))

    base.fts5.db_path = env_or("FTS5_DB_PATH", base.fts5.db_path)
    base.fts5.top_k_value = int(env_or("FTS5_TOP_K_VALUE", base.fts5.top_k_value))

    base.es.url = env_or("ES_URL", base.es.url)
    base.es.enabled = env_or("ES_ENABLED", "false").lower() in ("1", "true", "yes")

    base.llm.api_base = env_or("LLM_API_BASE", base.llm.api_base)
    base.llm.api_key = env_or("LLM_API_KEY", base.llm.api_key)
    base.llm.model = env_or("LLM_MODEL", base.llm.model)
    base.llm.temperature = float(env_or("LLM_TEMPERATURE", base.llm.temperature))
    base.llm.max_tokens = int(env_or("LLM_MAX_TOKENS", base.llm.max_tokens))

    base.cache.ttl_seconds = int(env_or("CACHE_TTL_SECONDS", base.cache.ttl_seconds))
    base.cache.similarity_threshold = float(env_or("CACHE_SIMILARITY_THRESHOLD", base.cache.similarity_threshold))

    cors_raw = env_or("CORS_ALLOW_ORIGINS", ",".join(getattr(base.cors, "allow_origins", ["*"])))
    base.cors.allow_origins = [x.strip() for x in cors_raw.split(",") if x.strip()]

    base.logging.dir = env_or("LOG_DIR", base.logging.dir)
    base.logging.level = env_or("LOG_LEVEL", base.logging.level)
    base.logging.retention_days = int(env_or("LOG_RETENTION_DAYS", base.logging.retention_days))

    base.request.id_header = env_or("REQUEST_ID_HEADER", base.request.id_header)

    base.ask.max_query_length = int(env_or("ASK_MAX_QUERY_LENGTH", base.ask.max_query_length))

    base.app.env = env
    return base


cfg = get_cfg()
