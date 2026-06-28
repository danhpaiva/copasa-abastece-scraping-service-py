import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from .browser import criar_contexto, extrair_links_noticias, extrair_texto_noticia, navegar_com_retry
from .cache import cache_carregar, cache_ja_processado, cache_registrar, cache_salvar
from .config import BASE_URL, TIMEOUT_GLOBAL_S
from .models import Interrupcao
from .output import exibir_resultado, formatar_datetime
from .parser import extrair_cidades, extrair_datas, processar_noticia
from .timeout import TimeoutError, timeout_global
from .utils import carregar_bairros, dentro_da_janela, normalizar

log = logging.getLogger(__name__)


def _filtrar_encerrados(interrupcoes: list[Interrupcao]) -> list[Interrupcao]:
    """Remove encerrados mais antigos que D-1; ativos e sem data de fim sempre passam."""
    ontem = (datetime.now() - timedelta(days=1)).date()
    resultado = []
    removidos = 0
    for it in interrupcoes:
        if it.esta_encerrada():
            data_fim = it.fim.date()  # type: ignore[union-attr]  # esta_encerrada garante fim não-nulo
            if data_fim < ontem:
                removidos += 1
                continue
        resultado.append(it)
    if removidos:
        log.info("%d alerta(s) encerrado(s) com mais de 1 dia ignorado(s).", removidos)
    return resultado


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


def monitorar(modo_json: bool = False, janela_dias: int = 14, timeout_s: int = TIMEOUT_GLOBAL_S, output: Optional[Path] = None) -> int:
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

    alertas = _filtrar_encerrados(_deduplicar(interrupcoes))
    exibir_resultado(alertas, modo_json, output)
    return 1 if alertas else 0


def monitorar_url_direta(url: str, modo_json: bool = False, output: Optional[Path] = None) -> int:
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
                    exibir_resultado([resultado], modo_json, output)
                    return 1
                else:
                    log.info("Nenhum bairro monitorado encontrado nesta notícia.")
                    inicio, fim = extrair_datas(texto)
                    cidades = extrair_cidades(texto)
                    log.info("Cidades mencionadas : %s", ", ".join(cidades) or "nenhuma")
                    log.info("Período identificado: %s → %s", formatar_datetime(inicio), formatar_datetime(fim))
                    exibir_resultado([], modo_json, output)
                    return 0

            finally:
                context.close()
                browser.close()

    except Exception as exc:
        log.error("Erro inesperado: %s", exc)
        return 2
