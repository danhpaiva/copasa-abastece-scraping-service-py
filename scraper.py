"""
Copasa Abastecimento Scraper
Monitora interrupções no abastecimento de água em bairros configurados.
"""

import re
import json
import random
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Garante UTF-8 no stdout mesmo em terminais Windows com cp1252/cp850
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright, Page, BrowserContext


# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------

BASE_URL = (
    "https://www.copasa.com.br/wps/portal/internet/imprensa/noticias/"
    "informacoes-sobre-abastecimento"
)

BAIRROS_FILE = Path(__file__).parent / "bairros.json"

# Seletores resilientes — tentados em ordem até encontrar conteúdo
ARTICLE_SELECTORS = [
    "article",
    ".ibm-columns",
    ".ibm-col-1-1",
    "main",
    "#WCM_GLOBAL_CONTEXT",
    "body",
]

# User-agents realistas para rotação
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
# Regex para extração de datas no formato da Copasa:
#   "do dia DD/MM/YYYY (HH:MM:SS) até o dia DD/MM/YYYY (HH:MM:SS)"
# O portal também pode usar variações sem segundos: (HH:MM)
# ---------------------------------------------------------------------------
DATE_PATTERN = re.compile(
    r"do\s+dia\s+(\d{2}/\d{2}/\d{4})\s*\((\d{2}:\d{2}(?::\d{2})?)\)"
    r".*?"
    r"at[eé]\s+o\s+dia\s+(\d{2}/\d{2}/\d{4})\s*\((\d{2}:\d{2}(?::\d{2})?)\)",
    re.IGNORECASE | re.DOTALL,
)

# Captura a cidade mencionada na notícia (ex.: "Cidades de BELO HORIZONTE , CONTAGEM")
CITIES_PATTERN = re.compile(
    r"(?:cidade[s]?\s+de|munic[ií]pio[s]?\s+de)\s+([A-ZÁÉÍÓÚÀÂÊÔÃÕÇ ,]+?)(?=[,.]|$|\n|\r|o\s+abastecimento)",
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


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def normalizar(texto: str) -> str:
    """Remove acentos e converte para minúsculas para comparação."""
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sem_acento.lower().strip()


def carregar_bairros() -> tuple[list[str], dict[str, str]]:
    """Carrega a lista de bairros e aliases do arquivo JSON."""
    with open(BAIRROS_FILE, encoding="utf-8") as f:
        dados = json.load(f)
    return dados["bairros"], dados.get("aliases", {})


def parse_datetime(data_str: str, hora_str: str) -> Optional[datetime]:
    """
    Converte strings de data e hora do formato Copasa para datetime.

    Formato esperado: data='28/06/2026', hora='06:00:00' ou '06:00'
    A Copasa usa DD/MM/YYYY (HH:MM:SS) — invertido em relação ao ISO 8601.
    """
    # Normaliza hora para sempre ter segundos
    if hora_str.count(":") == 1:
        hora_str += ":00"
    try:
        return datetime.strptime(f"{data_str} {hora_str}", "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Módulo de navegação (Playwright)
# ---------------------------------------------------------------------------

def criar_contexto(playwright) -> tuple:
    """Cria browser e contexto com fingerprint humano."""
    browser = playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    context = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": random.randint(1280, 1920), "height": random.randint(800, 1080)},
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        # Simula navegador real — não expõe webdriver
        java_script_enabled=True,
    )
    # Remove a propriedade que indica WebDriver automatizado
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, context


def navegar_com_retry(page: Page, url: str, tentativas: int = 3) -> bool:
    """Navega para a URL com retry e backoff exponencial."""
    import time

    for tentativa in range(1, tentativas + 1):
        try:
            # Simula movimento humano aguardando entre ações
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            # Aguarda JS do portal IBM WebSphere carregar
            page.wait_for_load_state("networkidle", timeout=15_000)
            return True
        except Exception as exc:
            print(f"  [WARN] Tentativa {tentativa}/{tentativas} falhou: {exc}")
            if tentativa < tentativas:
                espera = 2 ** tentativa + random.uniform(0, 1)
                print(f"  [INFO] Aguardando {espera:.1f}s antes de re-tentar...")
                time.sleep(espera)
    return False


def extrair_links_noticias(page: Page) -> list[dict]:
    """
    Extrai links e títulos das notícias da página de listagem.
    O portal IBM WCM usa estrutura de links dentro de .ibm-columns ou similares.
    """
    links = []
    try:
        # Aguarda ao menos um link de notícia aparecer
        page.wait_for_selector("a[href*='abast']", timeout=10_000)
        elementos = page.query_selector_all("a[href*='abast']")
        for el in elementos:
            href = el.get_attribute("href") or ""
            titulo = el.inner_text().strip()
            if href and titulo and len(titulo) > 10:
                # Resolve URL relativa
                if href.startswith("/"):
                    href = "https://www.copasa.com.br" + href
                links.append({"url": href, "titulo": titulo})
    except Exception as exc:
        print(f"  [WARN] Não foi possível extrair links: {exc}")
    return links


def extrair_texto_noticia(page: Page) -> str:
    """
    Extrai o texto principal da notícia tentando seletores em ordem de especificidade.
    Retorna o conteúdo de texto mais relevante encontrado.
    """
    for seletor in ARTICLE_SELECTORS:
        try:
            el = page.query_selector(seletor)
            if el:
                texto = el.inner_text().strip()
                # Considera válido se tiver conteúdo substancial
                if len(texto) > 100:
                    return texto
        except Exception:
            continue
    # Fallback: texto completo da página
    return page.inner_text("body")


# ---------------------------------------------------------------------------
# Módulo de parsing
# ---------------------------------------------------------------------------

def extrair_datas(texto: str) -> tuple[Optional[datetime], Optional[datetime]]:
    """
    Aplica o regex DATE_PATTERN para capturar início e fim da interrupção.

    Formato Copasa: "do dia DD/MM/YYYY (HH:MM:SS) até o dia DD/MM/YYYY (HH:MM:SS)"
    """
    match = DATE_PATTERN.search(texto)
    if not match:
        return None, None

    data_inicio, hora_inicio, data_fim, hora_fim = match.groups()
    inicio = parse_datetime(data_inicio, hora_inicio)
    fim = parse_datetime(data_fim, hora_fim)
    return inicio, fim


def extrair_cidades(texto: str) -> list[str]:
    """Extrai lista de cidades mencionadas no texto da notícia."""
    match = CITIES_PATTERN.search(texto)
    if not match:
        return []
    cidades_raw = match.group(1)
    # Divide por vírgula e normaliza espaços
    return [c.strip().title() for c in cidades_raw.split(",") if c.strip()]


def cruzar_bairros(texto: str, bairros: list[str], aliases: dict[str, str]) -> list[str]:
    """
    Verifica quais bairros da lista aparecem no corpo da notícia.
    Usa normalização (sem acento, minúsculas) para comparação robusta.
    Considera também os aliases definidos no JSON.
    """
    texto_norm = normalizar(texto)
    encontrados = []

    # Monta mapa de busca: normalizado → nome canônico
    mapa = {normalizar(b): b for b in bairros}
    for alias, canonical in aliases.items():
        mapa[normalizar(alias)] = canonical

    for norm_key, canonical in mapa.items():
        if norm_key in texto_norm and canonical not in encontrados:
            encontrados.append(canonical)

    return encontrados


def processar_noticia(url: str, titulo: str, texto: str, bairros: list[str], aliases: dict) -> Optional[Interrupcao]:
    """
    Processa o texto de uma notícia e retorna uma Interrupcao se relevante.
    Retorna None se não houver bairros monitorados afetados.
    """
    bairros_afetados = cruzar_bairros(texto, bairros, aliases)
    if not bairros_afetados:
        return None

    inicio, fim = extrair_datas(texto)
    cidades = extrair_cidades(texto)

    return Interrupcao(
        titulo=titulo,
        url=url,
        cidades=cidades,
        inicio=inicio,
        fim=fim,
        bairros_afetados=bairros_afetados,
        texto_bruto=texto,
    )


# ---------------------------------------------------------------------------
# Módulo de alerta
# ---------------------------------------------------------------------------

def formatar_datetime(dt: Optional[datetime]) -> str:
    if dt is None:
        return "não identificado"
    return dt.strftime("%d/%m/%Y às %H:%M")


def exibir_alerta(interrupcao: Interrupcao) -> None:
    """Imprime na tela as informações relevantes da interrupção."""
    separador = "=" * 70

    status = "ATIVA AGORA" if interrupcao.esta_ativa() else "PROGRAMADA / ENCERRADA"

    print(separador)
    print(f"  ALERTA DE INTERRUPÇÃO — {status}")
    print(separador)
    print(f"  Título   : {interrupcao.titulo}")
    print(f"  URL      : {interrupcao.url}")
    print()
    print(f"  Início   : {formatar_datetime(interrupcao.inicio)}")
    print(f"  Término  : {formatar_datetime(interrupcao.fim)}")
    print()
    print(f"  Cidades  : {', '.join(interrupcao.cidades) or 'não identificadas'}")
    print(f"  Bairros monitorados afetados: {', '.join(interrupcao.bairros_afetados)}")
    print(separador)
    print()


def exibir_sem_ocorrencias(bairros: list[str]) -> None:
    print("=" * 70)
    print("  Nenhuma interrupção encontrada para os bairros monitorados:")
    print(f"  {', '.join(bairros)}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Orquestrador principal
# ---------------------------------------------------------------------------

def monitorar() -> None:
    bairros, aliases = carregar_bairros()
    print(f"[INFO] Bairros monitorados: {', '.join(bairros)}")
    print(f"[INFO] Acessando: {BASE_URL}")
    print()

    interrupcoes: list[Interrupcao] = []

    with sync_playwright() as playwright:
        browser, context = criar_contexto(playwright)
        try:
            page = context.new_page()

            # 1. Navega para a listagem de notícias
            sucesso = navegar_com_retry(page, BASE_URL)
            if not sucesso:
                print("[ERRO] Não foi possível acessar o portal da Copasa.")
                return

            links = extrair_links_noticias(page)
            print(f"[INFO] {len(links)} notícia(s) encontrada(s) na listagem.")

            # 2. Para cada notícia, abre e processa
            for item in links[:10]:  # Limita às 10 mais recentes
                print(f"  [>>] {item['titulo'][:60]}...")
                try:
                    sucesso = navegar_com_retry(page, item["url"])
                    if not sucesso:
                        continue

                    texto = extrair_texto_noticia(page)
                    resultado = processar_noticia(
                        url=item["url"],
                        titulo=item["titulo"],
                        texto=texto,
                        bairros=bairros,
                        aliases=aliases,
                    )
                    if resultado:
                        interrupcoes.append(resultado)
                        print(f"      [MATCH] Bairros afetados: {resultado.bairros_afetados}")

                except Exception as exc:
                    print(f"  [WARN] Erro ao processar notícia: {exc}")

        finally:
            context.close()
            browser.close()

    # 3. Exibe alertas
    print()
    if interrupcoes:
        print(f"[RESULTADO] {len(interrupcoes)} alerta(s) encontrado(s):\n")
        for interrupcao in interrupcoes:
            exibir_alerta(interrupcao)
    else:
        exibir_sem_ocorrencias(bairros)


# ---------------------------------------------------------------------------
# Processamento direto de URL conhecida (modo rápido)
# ---------------------------------------------------------------------------

def monitorar_url_direta(url: str) -> None:
    """
    Processa uma URL específica de notícia diretamente,
    sem percorrer a listagem. Útil para alertas já identificados.
    """
    bairros, aliases = carregar_bairros()
    print(f"[INFO] Processando URL direta...")
    print(f"[INFO] Bairros monitorados: {', '.join(bairros)}")
    print()

    with sync_playwright() as playwright:
        browser, context = criar_contexto(playwright)
        try:
            page = context.new_page()
            sucesso = navegar_com_retry(page, url)
            if not sucesso:
                print("[ERRO] Não foi possível acessar a notícia.")
                return

            titulo = page.title() or "Notícia Copasa"
            texto = extrair_texto_noticia(page)

            resultado = processar_noticia(
                url=url,
                titulo=titulo,
                texto=texto,
                bairros=bairros,
                aliases=aliases,
            )

            print()
            if resultado:
                exibir_alerta(resultado)
            else:
                print("[INFO] Nenhum dos bairros monitorados foi encontrado nesta notícia.")
                print()
                # Mesmo sem match de bairro, exibe as datas encontradas para diagnóstico
                inicio, fim = extrair_datas(texto)
                cidades = extrair_cidades(texto)
                print(f"  Cidades mencionadas : {', '.join(cidades) or 'nenhuma'}")
                print(f"  Período identificado: {formatar_datetime(inicio)} → {formatar_datetime(fim)}")

        finally:
            context.close()
            browser.close()


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # Modo URL direta: python scraper.py <url>
        monitorar_url_direta(sys.argv[1])
    else:
        # Modo varredura da listagem completa
        monitorar()
