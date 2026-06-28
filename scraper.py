"""
Copasa Abastecimento Scraper
Monitora interrupções no abastecimento de água em bairros configurados.

Uso:
  python scraper.py                  # varredura completa, saída texto
  python scraper.py --json           # varredura completa, saída JSON em stdout
  python scraper.py <url>            # processa URL direta
  python scraper.py <url> --json     # URL direta com saída JSON

Exit codes:
  0 — nenhuma interrupção encontrada nos bairros monitorados
  1 — uma ou mais interrupções encontradas
  2 — erro de execução (rede, parsing, etc.)
"""

import argparse
import hashlib
import json
import logging
import random
import re
import signal
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Encoding — garante UTF-8 no stdout mesmo em terminais Windows cp1252/cp850
# ---------------------------------------------------------------------------
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright, Page


# ---------------------------------------------------------------------------
# Logging
# Logs de diagnóstico → stderr (nível INFO por padrão)
# Resultado final    → stdout (via print ou json.dumps)
# Assim, em modo --json, stdout fica limpo para consumo por máquina.
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(levelname)-8s %(message)s",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------

BASE_URL = (
    "https://www.copasa.com.br/wps/portal/internet/imprensa/noticias/"
    "informacoes-sobre-abastecimento"
)

BAIRROS_FILE = Path(__file__).parent / "bairros.json"
CACHE_FILE   = Path(__file__).parent / ".cache.json"

# Timeout global de sessão em segundos.
# Protege execuções automáticas (cron) contra portais travados.
TIMEOUT_GLOBAL_S = 180

ARTICLE_SELECTORS = [
    "article",
    ".ibm-columns",
    ".ibm-col-1-1",
    "main",
    "#WCM_GLOBAL_CONTEXT",
    "body",
]

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
        "Gecko/20100101 Firefox/126.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4.1 Safari/605.1.15"
    ),
]

# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

# "do dia DD/MM/YYYY (HH:MM:SS) até o dia DD/MM/YYYY (HH:MM:SS)"
DATE_PATTERN = re.compile(
    r"do\s+dia\s+(\d{2}/\d{2}/\d{4})\s*\((\d{2}:\d{2}(?::\d{2})?)\)"
    r".*?"
    r"at[eé]\s+o\s+dia\s+(\d{2}/\d{2}/\d{4})\s*\((\d{2}:\d{2}(?::\d{2})?)\)",
    re.IGNORECASE | re.DOTALL,
)

# DD/MM no início do título
TITLE_DATE_PATTERN = re.compile(r"^(\d{2}/\d{2})")

# Lista de cidades após "Cidades de" / "Municípios de"
CITIES_PATTERN = re.compile(
    r"(?:cidade[s]?\s+de|munic[ií]pio[s]?\s+de)\s+([A-ZÁÉÍÓÚÀÂÊÔÃÕÇ\s,e]+?)(?=\.|$|\r|\n|o\s+abastecimento)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Modelos de dados
# ---------------------------------------------------------------------------

@dataclass
class Interrupcao:
    titulo: str
    url: str
    cidades: list[str]
    inicio: Optional[datetime]
    fim: Optional[datetime]
    bairros_afetados: list[str]
    texto_bruto: str = field(repr=False)

    def esta_ativa(self) -> bool:
        agora = datetime.now()
        if self.inicio and self.fim:
            return self.inicio <= agora <= self.fim
        return False

    def to_dict(self) -> dict:
        return {
            "titulo": self.titulo,
            "url": self.url,
            "cidades": self.cidades,
            "inicio": self.inicio.isoformat() if self.inicio else None,
            "fim": self.fim.isoformat() if self.fim else None,
            "esta_ativa": self.esta_ativa(),
            "bairros_afetados": self.bairros_afetados,
        }


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def normalizar(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sem_acento.lower().strip()


def carregar_bairros() -> tuple[list[str], dict[str, str], list[str]]:
    with open(BAIRROS_FILE, encoding="utf-8") as f:
        dados = json.load(f)
    return dados["bairros"], dados.get("aliases", {}), dados.get("cidades_alvo", [])


def parse_datetime(data_str: str, hora_str: str) -> Optional[datetime]:
    """
    Converte data e hora do formato Copasa para datetime.
    Formato: data='28/06/2026', hora='06:00:00' ou '06:00'
    """
    if hora_str.count(":") == 1:
        hora_str += ":00"
    try:
        return datetime.strptime(f"{data_str} {hora_str}", "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return None


def dentro_da_janela(titulo: str, janela_dias: int = 14) -> bool:
    """
    Retorna True se a data do título estiver dentro dos últimos `janela_dias`.
    Fail-open: títulos sem data passam pelo filtro.
    """
    match = TITLE_DATE_PATTERN.match(titulo.strip())
    if not match:
        return True

    hoje = datetime.now().date()
    try:
        data = datetime.strptime(f"{match.group(1)}/{hoje.year}", "%d/%m/%Y").date()
        if (data - hoje).days > 30:
            data = data.replace(year=hoje.year - 1)
    except ValueError:
        return True

    return (hoje - data).days <= janela_dias


# ---------------------------------------------------------------------------
# Cache de execução
# ---------------------------------------------------------------------------

def _url_hash(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()


def cache_carregar() -> dict:
    """Carrega o cache do disco; retorna dict vazio se não existir ou estiver corrompido."""
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def cache_salvar(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def cache_ja_processado(cache: dict, url: str) -> bool:
    """Retorna True apenas para artigos sem alerta — alertas são sempre re-checados."""
    entrada = cache.get(_url_hash(url))
    if entrada is None:
        return False
    return not entrada.get("teve_alerta", False)


def cache_registrar(cache: dict, url: str, resultado: Optional[dict]) -> None:
    """Grava URL processada com timestamp e resultado resumido."""
    cache[_url_hash(url)] = {
        "url": url,
        "processado_em": datetime.now().isoformat(),
        "teve_alerta": resultado is not None,
    }


# ---------------------------------------------------------------------------
# Timeout global
# ---------------------------------------------------------------------------

class TimeoutError(Exception):
    pass


def _handler_timeout(signum, frame):
    raise TimeoutError("Timeout global atingido.")


class timeout_global:
    """Context manager para timeout global. Usa SIGALRM no Unix; no Windows faz no-op."""

    def __init__(self, segundos: int):
        self.segundos = segundos
        self._suportado = hasattr(signal, "SIGALRM")

    def __enter__(self):
        if self._suportado:
            signal.signal(signal.SIGALRM, _handler_timeout)
            signal.alarm(self.segundos)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._suportado:
            signal.alarm(0)
        return False


# ---------------------------------------------------------------------------
# Navegação (Playwright)
# ---------------------------------------------------------------------------

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
    import time

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
      1º link → título   ("28/06 - BELO HORIZONTE - Situação do Abastecimento")
      2º link → resumo   ("A Copasa informa que, devido a...")

    O texto completo disponível na listagem evita navegar para cada artigo.
    """
    BASE = "https://www.copasa.com.br/wps/portal/internet/imprensa/noticias/informacoes-sobre-abastecimento"
    UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

    noticias: dict[str, dict] = {}

    try:
        elementos = page.query_selector_all("a[href*='urile']")
        for el in elementos:
            href = el.get_attribute("href") or ""
            texto = el.inner_text().strip()
            if not texto:
                continue

            uuid_match = UUID_RE.search(href)
            if not uuid_match:
                continue
            uuid = uuid_match.group(0)

            url_absoluta = f"{BASE}/{href}" if href.startswith("?") else href

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


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def extrair_datas(texto: str) -> tuple[Optional[datetime], Optional[datetime]]:
    match = DATE_PATTERN.search(texto)
    if not match:
        return None, None
    data_inicio, hora_inicio, data_fim, hora_fim = match.groups()
    return parse_datetime(data_inicio, hora_inicio), parse_datetime(data_fim, hora_fim)


def extrair_cidades(texto: str) -> list[str]:
    match = CITIES_PATTERN.search(texto)
    if not match:
        return []
    partes = re.split(r",|\be\b", match.group(1), flags=re.IGNORECASE)
    return [c.strip().title() for c in partes if c.strip()]


def cruzar_bairros(texto: str, bairros: list[str], aliases: dict[str, str]) -> list[str]:
    texto_norm = normalizar(texto)
    mapa = {normalizar(b): b for b in bairros}
    for alias, canonical in aliases.items():
        mapa[normalizar(alias)] = canonical

    encontrados = []
    for norm_key, canonical in mapa.items():
        if norm_key in texto_norm and canonical not in encontrados:
            encontrados.append(canonical)
    return encontrados


def processar_noticia(url: str, titulo: str, texto: str, bairros: list[str], aliases: dict) -> Optional[Interrupcao]:
    bairros_afetados = cruzar_bairros(texto, bairros, aliases)
    if not bairros_afetados:
        return None
    inicio, fim = extrair_datas(texto)
    cidades = extrair_cidades(texto)
    return Interrupcao(
        titulo=titulo, url=url, cidades=cidades,
        inicio=inicio, fim=fim,
        bairros_afetados=bairros_afetados, texto_bruto=texto,
    )


# ---------------------------------------------------------------------------
# Saída
# ---------------------------------------------------------------------------

def formatar_datetime(dt: Optional[datetime]) -> str:
    return dt.strftime("%d/%m/%Y às %H:%M") if dt else "não identificado"


def exibir_alerta_texto(interrupcao: Interrupcao) -> None:
    sep = "=" * 70
    status = "ATIVA AGORA" if interrupcao.esta_ativa() else "PROGRAMADA / ENCERRADA"
    print(sep)
    print(f"  ALERTA DE INTERRUPÇÃO — {status}")
    print(sep)
    print(f"  Título   : {interrupcao.titulo}")
    print(f"  URL      : {interrupcao.url}")
    print()
    print(f"  Início   : {formatar_datetime(interrupcao.inicio)}")
    print(f"  Término  : {formatar_datetime(interrupcao.fim)}")
    print()
    print(f"  Cidades  : {', '.join(interrupcao.cidades) or 'não identificadas'}")
    print(f"  Bairros monitorados afetados: {', '.join(interrupcao.bairros_afetados)}")
    print(sep)
    print()


def exibir_resultado(interrupcoes: list[Interrupcao], modo_json: bool) -> None:
    if modo_json:
        print(json.dumps(
            [i.to_dict() for i in interrupcoes],
            ensure_ascii=False,
            indent=2,
        ))
        return

    if not interrupcoes:
        print("=" * 70)
        print("  Nenhuma interrupção encontrada para os bairros monitorados.")
        print("=" * 70)
        return

    print(f"[RESULTADO] {len(interrupcoes)} alerta(s) único(s) encontrado(s):\n")
    for it in interrupcoes:
        exibir_alerta_texto(it)


# ---------------------------------------------------------------------------
# Orquestrador
# ---------------------------------------------------------------------------

def _deduplicar(interrupcoes: list[Interrupcao]) -> list[Interrupcao]:
    unicos: dict[tuple, Interrupcao] = {}
    for it in interrupcoes:
        chave = (it.inicio, it.fim, frozenset(it.cidades))
        if chave not in unicos or len(it.titulo) > len(unicos[chave].titulo):
            unicos[chave] = it
    duplicatas = len(interrupcoes) - len(unicos)
    if duplicatas:
        log.info("%d publicação(ões) duplicada(s) ignorada(s).", duplicatas)
    return list(unicos.values())


def monitorar(modo_json: bool = False, janela_dias: int = 14, timeout_s: int = TIMEOUT_GLOBAL_S) -> int:
    """Retorna exit code: 1 se há alertas, 0 se não há, 2 se erro."""
    try:
        bairros, aliases, cidades_alvo = carregar_bairros()
    except Exception as exc:
        log.error("Falha ao carregar bairros.json: %s", exc)
        return 2

    log.info("Bairros monitorados : %s", ", ".join(bairros))
    log.info("Cidades-alvo        : %s", ", ".join(cidades_alvo) or "(todas)")
    log.info("Janela de datas     : %d dias", janela_dias)
    log.info("Timeout global      : %ds", timeout_s)
    log.info("Acessando           : %s", BASE_URL)

    cidades_norm = [normalizar(c) for c in cidades_alvo]
    interrupcoes: list[Interrupcao] = []
    cache = cache_carregar()
    cache_hits = 0

    try:
        with timeout_global(timeout_s):
            with sync_playwright() as playwright:
                browser, context = criar_contexto(playwright)
                try:
                    page = context.new_page()

                    if not navegar_com_retry(page, BASE_URL):
                        log.error("Não foi possível acessar o portal da Copasa.")
                        return 2

                    noticias = extrair_links_noticias(page)
                    log.info("%d notícia(s) encontrada(s) na listagem.", len(noticias))

                    candidatos = []
                    ignorados_data = 0
                    for item in noticias:
                        if not dentro_da_janela(item["titulo"], janela_dias):
                            ignorados_data += 1
                            continue
                        texto_norm = normalizar(item["texto"])
                        if not cidades_norm or any(c in texto_norm for c in cidades_norm):
                            candidatos.append(item)

                    if ignorados_data:
                        log.info("%d notícia(s) fora da janela de %d dias ignorada(s).", ignorados_data, janela_dias)
                    log.info("%d artigo(s) com cidade-alvo para inspeção detalhada.", len(candidatos))

                    for i, item in enumerate(candidatos, 1):
                        if cache_ja_processado(cache, item["url"]):
                            cache_hits += 1
                            log.debug("[%d/%d] CACHE HIT — %s", i, len(candidatos), item["titulo"][:60])
                            continue

                        log.info("[%d/%d] %s...", i, len(candidatos), item["titulo"][:65])
                        resultado = None
                        try:
                            if not navegar_com_retry(page, item["url"]):
                                continue
                            texto_completo = extrair_texto_noticia(page)
                            resultado = processar_noticia(
                                url=item["url"], titulo=item["titulo"],
                                texto=texto_completo, bairros=bairros, aliases=aliases,
                            )
                            if resultado:
                                interrupcoes.append(resultado)
                                log.info("  MATCH → bairros: %s", resultado.bairros_afetados)
                            else:
                                log.debug("  SKIP — nenhum bairro monitorado encontrado.")
                        except Exception as exc:
                            log.warning("Erro ao processar artigo: %s", exc)
                            continue

                        cache_registrar(cache, item["url"], resultado.to_dict() if resultado else None)

                    cache_salvar(cache)
                    if cache_hits:
                        log.info("%d artigo(s) ignorado(s) por cache.", cache_hits)

                finally:
                    context.close()
                    browser.close()

    except TimeoutError:
        log.error("Timeout global de %ds atingido. Encerrando.", timeout_s)
        cache_salvar(cache)
        return 2
    except Exception as exc:
        log.error("Erro inesperado: %s", exc)
        return 2

    alertas = _deduplicar(interrupcoes)
    exibir_resultado(alertas, modo_json)
    return 1 if alertas else 0


def monitorar_url_direta(url: str, modo_json: bool = False) -> int:
    """Retorna exit code: 1 se há alerta, 0 se não há, 2 se erro."""
    try:
        bairros, aliases, _ = carregar_bairros()
    except Exception as exc:
        log.error("Falha ao carregar bairros.json: %s", exc)
        return 2

    log.info("Bairros monitorados: %s", ", ".join(bairros))
    log.info("URL: %s", url)

    try:
        with sync_playwright() as playwright:
            browser, context = criar_contexto(playwright)
            try:
                page = context.new_page()
                if not navegar_com_retry(page, url):
                    log.error("Não foi possível acessar a notícia.")
                    return 2

                titulo = page.title() or "Notícia Copasa"
                texto = extrair_texto_noticia(page)
                resultado = processar_noticia(
                    url=url, titulo=titulo, texto=texto,
                    bairros=bairros, aliases=aliases,
                )

                if resultado:
                    exibir_resultado([resultado], modo_json)
                    return 1
                else:
                    log.info("Nenhum bairro monitorado encontrado nesta notícia.")
                    inicio, fim = extrair_datas(texto)
                    cidades = extrair_cidades(texto)
                    log.info("Cidades mencionadas : %s", ", ".join(cidades) or "nenhuma")
                    log.info("Período identificado: %s → %s", formatar_datetime(inicio), formatar_datetime(fim))
                    if modo_json:
                        print("[]")
                    return 0

            finally:
                context.close()
                browser.close()

    except Exception as exc:
        log.error("Erro inesperado: %s", exc)
        return 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitora interrupções de abastecimento da Copasa.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  nenhuma interrupção encontrada\n"
            "  1  uma ou mais interrupções encontradas\n"
            "  2  erro de execução\n"
        ),
    )
    parser.add_argument("url", nargs="?", help="URL direta de uma notícia (opcional)")
    parser.add_argument("--json", dest="json_output", action="store_true",
                        help="Emite resultado em JSON para stdout (logs vão para stderr)")
    parser.add_argument("--janela", type=int, default=14, metavar="DIAS",
                        help="Janela de dias para filtrar notícias pelo título (padrão: 14)")
    parser.add_argument("--no-cache", dest="no_cache", action="store_true",
                        help="Ignora o cache e reprocessa todos os artigos")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_GLOBAL_S, metavar="SEG",
                        help=f"Timeout global da sessão em segundos (padrão: {TIMEOUT_GLOBAL_S})")
    parser.add_argument("--debug", action="store_true",
                        help="Habilita logs de nível DEBUG")
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.no_cache and CACHE_FILE.exists():
        CACHE_FILE.unlink()
        log.info("Cache limpo.")

    if args.url:
        code = monitorar_url_direta(args.url, modo_json=args.json_output)
    else:
        code = monitorar(modo_json=args.json_output, janela_dias=args.janela, timeout_s=args.timeout)

    sys.exit(code)
