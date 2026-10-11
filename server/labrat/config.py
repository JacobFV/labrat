import logging
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "server" / ".env")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("labrat")


def env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if v not in (None, "") else default


TOKEN = env("LABRAT_TOKEN", "labrat")
LLM_PROVIDER = (env("LLM_PROVIDER", "anthropic") or "anthropic").lower()
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", "claude-sonnet-5")
FIREWORKS_MODEL = env("FIREWORKS_MODEL", "accounts/fireworks/models/llama4-maverick-instruct-basic")
AUTO_APPROVE = env("LABRAT_AUTO_APPROVE", "0") in ("1", "true", "yes")
WEB_DIR = Path(env("LABRAT_WEB_DIR", str(ROOT / "web")))
PHONE_DIR = Path(env("LABRAT_PHONE_DIR", str(ROOT / "phone")))

_warned: set[str] = set()


def warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(msg)
