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

# Extrai DD/MM do título da notícia (ex.: "28/06 - BELO HORIZONTE...")
TITLE_DATE_PATTERN = re.compile(r"^(\d{2}/\d{2})")

# Captura todas as cidades da lista separada por vírgulas/espaços após "Cidades de" ou "Municípios de".
# O lookahead termina em "o abastecimento" ou ponto/fim-de-linha — não em vírgula,
# pois a própria lista usa vírgulas como separador.
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


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def normalizar(texto: str) -> str:
    """Remove acentos e converte para minúsculas para comparação."""
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sem_acento.lower().strip()


def carregar_bairros() -> tuple[list[str], dict[str, str], list[str]]:
    """Carrega bairros, aliases e cidades-alvo do arquivo JSON."""
    with open(BAIRROS_FILE, encoding="utf-8") as f:
        dados = json.load(f)
    return dados["bairros"], dados.get("aliases", {}), dados.get("cidades_alvo", [])


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


def dentro_da_janela(titulo: str, janela_dias: int = 14) -> bool:
    """
    Retorna True se a data extraída do título estiver dentro dos últimos `janela_dias`.

    O título segue o padrão "DD/MM - CIDADE...", sem ano. O ano é inferido:
    como o portal só exibe notícias recentes, assume-se o ano corrente; se a
    data resultante estiver no futuro (ex.: publicação em janeiro, referência
    a dezembro do ano anterior), recua um ano.
    """
    match = TITLE_DATE_PATTERN.match(titulo.strip())
    if not match:
        return True  # Sem data no título → não descarta (fail-open)

    hoje = datetime.now().date()
    try:
        data = datetime.strptime(f"{match.group(1)}/{hoje.year}", "%d/%m/%Y").date()
        # Se a data inferida estiver mais de 30 dias no futuro, é do ano anterior
        if (data - hoje).days > 30:
            data = data.replace(year=hoje.year - 1)
    except ValueError:
        return True

    return (hoje - data).days <= janela_dias


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
    Extrai notícias da página de listagem do portal IBM WCM da Copasa.

    O portal renderiza cada notícia com DOIS links consecutivos com o mesmo href
    (formato ?1dmy&urile=wcm%3apath%3a%2F.../<UUID>):
      - 1º link: título  ("28/06 - BELO HORIZONTE - Situação do Abastecimento")
      - 2º link: resumo  ("A Copasa informa que, devido a...")

    Como o texto completo já está disponível na listagem, não é necessário
    navegar para cada artigo individualmente.
    """
    BASE = "https://www.copasa.com.br/wps/portal/internet/imprensa/noticias/informacoes-sobre-abastecimento"
    UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

    noticias: dict[str, dict] = {}  # uuid → {titulo, resumo, url}

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
                # Segundo link = resumo; concatena ao título para formar o texto completo
                noticias[uuid]["resumo"] = texto

    except Exception as exc:
        print(f"  [WARN] Erro ao extrair links: {exc}")

    # Monta lista com texto combinado (título + resumo)
    resultado = []
    for item in noticias.values():
        resultado.append({
            "url": item["url"],
            "titulo": item["titulo"],
            "texto": f"{item['titulo']}\n{item['resumo']}",
        })
    return resultado


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
    # Remove conjunção "e" isolada, divide por vírgula, normaliza espaços
    partes = re.split(r",|\be\b", cidades_raw, flags=re.IGNORECASE)
    return [c.strip().title() for c in partes if c.strip()]


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
    bairros, aliases, cidades_alvo = carregar_bairros()
    print(f"[INFO] Bairros monitorados : {', '.join(bairros)}")
    print(f"[INFO] Cidades-alvo        : {', '.join(cidades_alvo) or '(todas)'}")
    print(f"[INFO] Acessando           : {BASE_URL}")
    print()

    interrupcoes: list[Interrupcao] = []
    cidades_norm = [normalizar(c) for c in cidades_alvo]

    with sync_playwright() as playwright:
        browser, context = criar_contexto(playwright)
        try:
            page = context.new_page()

            # 1. Carrega a listagem de notícias
            sucesso = navegar_com_retry(page, BASE_URL)
            if not sucesso:
                print("[ERRO] Não foi possível acessar o portal da Copasa.")
                return

            noticias = extrair_links_noticias(page)
            print(f"[INFO] {len(noticias)} notícia(s) encontrada(s) na listagem.")

            # 2. Primeiro passo: filtra na listagem por janela de datas e cidade-alvo.
            #    Nenhuma navegação extra — usa título e resumo já disponíveis.
            candidatos = []
            ignorados_data = 0
            for item in noticias:
                if not dentro_da_janela(item["titulo"]):
                    ignorados_data += 1
                    continue
                texto_norm = normalizar(item["texto"])
                cidade_ok = not cidades_norm or any(c in texto_norm for c in cidades_norm)
                if cidade_ok:
                    candidatos.append(item)

            if ignorados_data:
                print(f"[INFO] {ignorados_data} notícia(s) fora da janela de 14 dias ignorada(s).")
            total = len(candidatos)
            print(f"[INFO] {total} artigo(s) com cidade-alvo para inspeção detalhada.")
            print()

            # 3. Segundo passo: navega para cada candidato e verifica bairros no texto completo
            for i, item in enumerate(candidatos, 1):
                print(f"  [{i}/{total}] {item['titulo'][:65]}...")
                try:
                    sucesso = navegar_com_retry(page, item["url"])
                    if not sucesso:
                        continue

                    texto_completo = extrair_texto_noticia(page)
                    resultado = processar_noticia(
                        url=item["url"],
                        titulo=item["titulo"],
                        texto=texto_completo,
                        bairros=bairros,
                        aliases=aliases,
                    )
                    if resultado:
                        interrupcoes.append(resultado)
                        print(f"        [MATCH] Bairros afetados: {resultado.bairros_afetados}")
                    else:
                        print(f"        [SKIP] Nenhum bairro monitorado encontrado.")

                except Exception as exc:
                    print(f"  [WARN] Erro ao processar artigo: {exc}")

        finally:
            context.close()
            browser.close()

    # 4. Deduplica: mesmo (inicio, fim) = mesma interrupção republicada no portal
    unicos: dict[tuple, Interrupcao] = {}
    for it in interrupcoes:
        chave = (it.inicio, it.fim, frozenset(it.cidades))
        if chave not in unicos:
            unicos[chave] = it
        else:
            # Mantém o título mais longo (mais completo)
            if len(it.titulo) > len(unicos[chave].titulo):
                unicos[chave] = it

    duplicatas = len(interrupcoes) - len(unicos)
    if duplicatas:
        print(f"[INFO] {duplicatas} publicação(ões) duplicada(s) ignorada(s).")

    # 5. Exibe alertas
    print()
    if unicos:
        print(f"[RESULTADO] {len(unicos)} alerta(s) único(s) encontrado(s):\n")
        for interrupcao in unicos.values():
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
    bairros, aliases, _ = carregar_bairros()
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
