import logging
import random
import re
import time

from playwright.sync_api import Page

from .config import ARTICLE_SELECTORS, BASE_URL, USER_AGENTS

log = logging.getLogger(__name__)

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def criar_contexto(playwright):
    browser = playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": random.randint(1280, 1920), "height": random.randint(800, 1080)},
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        java_script_enabled=True,
    )
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, context


def navegar_com_retry(page: Page, url: str, tentativas: int = 3) -> bool:
    for tentativa in range(1, tentativas + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_load_state("networkidle", timeout=15_000)
            return True
        except Exception as exc:
            log.warning("Tentativa %d/%d falhou: %s", tentativa, tentativas, exc)
            if tentativa < tentativas:
                espera = 2 ** tentativa + random.uniform(0, 1)
                log.info("Aguardando %.1fs antes de re-tentar...", espera)
                time.sleep(espera)
    return False


def extrair_links_noticias(page: Page) -> list[dict]:
    """
    Extrai notícias da listagem do portal IBM WCM.

    Cada notícia aparece em dois links consecutivos com o mesmo UUID no href:
      1º link → título
      2º link → resumo

    O texto completo disponível na listagem evita navegar para cada artigo.
    """
    noticias: dict[str, dict] = {}

    try:
        elementos = page.query_selector_all("a[href*='urile']")
        for el in elementos:
            href = el.get_attribute("href") or ""
            texto = el.inner_text().strip()
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


def extrair_texto_noticia(page: Page) -> str:
    for seletor in ARTICLE_SELECTORS:
        try:
            el = page.query_selector(seletor)
            if el:
                texto = el.inner_text().strip()
                if len(texto) > 100:
                    return texto
        except Exception:
            continue
    return page.inner_text("body")
