import logging
import random
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .config import ARTICLE_SELECTORS, BASE_URL, USER_AGENTS

log = logging.getLogger(__name__)

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def criar_sessao() -> requests.Session:
    sessao = requests.Session()
    sessao.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return sessao


def navegar_com_retry(sessao: requests.Session, url: str, tentativas: int = 3) -> Optional[BeautifulSoup]:
    """
    Busca a página via HTTP simples (o portal da Copasa é renderizado no
    servidor — não há necessidade de executar JavaScript).
    """
    for tentativa in range(1, tentativas + 1):
        try:
            resposta = sessao.get(url, timeout=30)
            resposta.raise_for_status()
            return BeautifulSoup(resposta.text, "html.parser")
        except Exception as exc:
            log.warning("Tentativa %d/%d falhou: %s", tentativa, tentativas, exc)
            if tentativa < tentativas:
                espera = 2 ** tentativa + random.uniform(0, 1)
                log.info("Aguardando %.1fs antes de re-tentar...", espera)
                time.sleep(espera)
    return None


def extrair_links_noticias(soup: BeautifulSoup) -> list[dict]:
    """
    Extrai notícias da listagem do portal IBM WCM.

    Cada notícia aparece em dois links consecutivos com o mesmo UUID no href:
      1º link → título
      2º link → resumo

    O texto completo disponível na listagem evita navegar para cada artigo.
    """
    noticias: dict[str, dict] = {}

    try:
        elementos = soup.select("a[href*='urile']")
        for el in elementos:
            href = el.get("href") or ""
            texto = el.get_text(strip=True)
            if not texto:
                continue

            uuid_match = _UUID_RE.search(href)
            if not uuid_match:
                continue
            uuid = uuid_match.group(0)

            url_absoluta = f"{BASE_URL}/{href}" if href.startswith("?") else href

            if uuid not in noticias:
                noticias[uuid] = {"titulo": texto, "resumo": "", "url": url_absoluta}
            else:
                noticias[uuid]["resumo"] = texto

    except Exception as exc:
        log.warning("Erro ao extrair links: %s", exc)

    return [
        {"url": v["url"], "titulo": v["titulo"], "texto": f"{v['titulo']}\n{v['resumo']}"}
        for v in noticias.values()
    ]


def extrair_texto_noticia(soup: BeautifulSoup) -> str:
    for seletor in ARTICLE_SELECTORS:
        try:
            el = soup.select_one(seletor)
            if el:
                texto = el.get_text(separator="\n", strip=True)
                if len(texto) > 100:
                    return texto
        except Exception:
            continue
    return soup.get_text(separator="\n", strip=True)
