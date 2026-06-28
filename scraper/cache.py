import hashlib
import json
from datetime import datetime
from typing import Optional

from .config import CACHE_FILE


def _url_hash(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()


def cache_carregar() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def cache_salvar(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def cache_ja_processado(cache: dict, url: str) -> bool:
    entrada = cache.get(_url_hash(url))
    if entrada is None:
        return False
    return not entrada.get("teve_alerta", False)


def cache_registrar(cache: dict, url: str, resultado: Optional[dict]) -> None:
    cache[_url_hash(url)] = {
        "url": url,
        "processado_em": datetime.now().isoformat(),
        "teve_alerta": resultado is not None,
    }
