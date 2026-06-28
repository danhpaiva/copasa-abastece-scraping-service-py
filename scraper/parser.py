import logging
import re
from typing import Optional

from .config import (
    BAIRROS_AFETADOS_HEADER,
    BAIRROS_CIDADE_PATTERN,
    CITIES_PATTERN,
    DATE_PATTERN,
)
from .models import Interrupcao
from .utils import normalizar, parse_datetime

log = logging.getLogger(__name__)


def extrair_datas(texto: str) -> tuple[Optional[object], Optional[object]]:
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


def extrair_bairros_do_texto(texto: str) -> list[str]:
    """
    Extrai bairros da seção "BAIRROS AFETADOS".

    Suporta dois formatos:
      Formato 1 — "NomeCidade: bairro1, bairro2, ..."
      Formato 2 — lista plana "bairro1, bairro2 e bairro3"
    """
    if not BAIRROS_AFETADOS_HEADER.search(texto):
        return []

    pos = BAIRROS_AFETADOS_HEADER.search(texto).start()
    trecho = texto[pos:]

    bairros: list[str] = []
    vistos: set[str] = set()

    matches = list(BAIRROS_CIDADE_PATTERN.finditer(trecho))
    if matches:
        for match in matches:
            for b in match.group(2).split(","):
                nome = b.strip().strip(".")
                if ":" in nome:
                    nome = nome.split(":")[-1].strip().strip(".")
                if nome:
                    nome_title = nome.title()
                    if nome_title not in vistos:
                        vistos.add(nome_title)
                        bairros.append(nome_title)
    else:
        linhas = trecho.splitlines()
        linha_bairros = next((l.strip() for l in linhas[1:] if l.strip()), "")
        if linha_bairros:
            fragmentos = linha_bairros.split(",")
            bairros_raw: list[str] = []
            for i, frag in enumerate(fragmentos):
                if i == len(fragmentos) - 1 and " e " in frag:
                    bairros_raw.extend(frag.split(" e "))
                else:
                    bairros_raw.append(frag)

            for nome in bairros_raw:
                nome = nome.strip()
                if nome:
                    nome_title = nome.title()
                    if nome_title not in vistos:
                        vistos.add(nome_title)
                        bairros.append(nome_title)

    if not bairros:
        log.warning("BAIRROS AFETADOS: cabeçalho encontrado mas nenhum bairro extraído — possível novo formato.")

    return bairros


def processar_noticia(url: str, titulo: str, texto: str, bairros: list[str], aliases: dict) -> Optional[Interrupcao]:
    if not bairros:
        bairros_afetados = extrair_bairros_do_texto(texto)
    else:
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
