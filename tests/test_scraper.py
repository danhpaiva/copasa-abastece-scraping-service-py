"""
Testes unitários para as funções puras do scraper.
Não dependem de Playwright nem de rede.

Execução:
    pytest tests/ -v
"""

import logging
from datetime import datetime, date, timedelta
from unittest.mock import patch

from scraper.utils import BRT

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from scraper.parser import (
    extrair_datas,
    extrair_cidades,
    extrair_bairros_do_texto,
    cruzar_bairros,
    processar_noticia,
)
from scraper.utils import dentro_da_janela
from scraper.cache import (
    cache_carregar,
    cache_ja_processado,
    cache_registrar,
    cache_salvar,
    _url_hash,
)
from scraper.monitor import _filtrar_encerrados
from scraper.models import Interrupcao


# ---------------------------------------------------------------------------
# Fixtures compartilhadas
# ---------------------------------------------------------------------------

BAIRROS = ["Nazare", "Sao Gabriel", "Vista do Sol"]

ALIASES = {
    "Nazaré": "Nazare",
    "São Gabriel": "Sao Gabriel",
    "NAZARE": "Nazare",
    "SAO GABRIEL": "Sao Gabriel",
    "VISTA DO SOL": "Vista do Sol",
}

TEXTO_NOTICIA = (
    "A Copasa informa que, devido a manutenção operacional programada nas "
    "Cidades de BELO HORIZONTE , CONTAGEM, NOVA LIMA, RAPOSOS, RIBEIRÃO DAS NEVES, "
    "SABARÁ, SANTA LUZIA, VESPASIANO, o abastecimento de água poderá apresentar "
    "intermitência, do dia 28/06/2026 (06:00:00) até o dia 30/06/2026 (07:00:00)\n"
    "Bairros afetados: Nazare, Sao Gabriel, Vista do Sol."
)


# ===========================================================================
# extrair_datas
# ===========================================================================

class TestExtrairDatas:

    def test_extrai_inicio_e_fim_com_segundos(self):
        inicio, fim = extrair_datas(TEXTO_NOTICIA)
        assert inicio == datetime(2026, 6, 28, 6, 0, 0, tzinfo=BRT)
        assert fim    == datetime(2026, 6, 30, 7, 0, 0, tzinfo=BRT)

    def test_extrai_hora_sem_segundos(self):
        texto = (
            "do dia 10/07/2026 (08:00) até o dia 11/07/2026 (18:30)"
        )
        inicio, fim = extrair_datas(texto)
        assert inicio == datetime(2026, 7, 10, 8, 0, 0, tzinfo=BRT)
        assert fim    == datetime(2026, 7, 11, 18, 30, 0, tzinfo=BRT)

    def test_retorna_none_none_sem_padrao(self):
        inicio, fim = extrair_datas("Texto sem nenhuma data.")
        assert inicio is None
        assert fim    is None

    def test_ignora_texto_parcial(self):
        # Apenas "do dia" sem "até o dia"
        inicio, fim = extrair_datas("do dia 01/01/2026 (10:00:00) e mais nada.")
        assert inicio is None
        assert fim    is None

    def test_aceita_variacao_ate_com_acento(self):
        texto = (
            "do dia 05/08/2026 (07:00:00) "
            "até o dia 06/08/2026 (12:00:00)"
        )
        inicio, fim = extrair_datas(texto)
        assert inicio is not None
        assert fim    is not None

    def test_aceita_variacao_ate_sem_acento(self):
        texto = (
            "do dia 05/08/2026 (07:00:00) "
            "ate o dia 06/08/2026 (12:00:00)"
        )
        inicio, fim = extrair_datas(texto)
        assert inicio is not None
        assert fim    is not None

    def test_data_invalida_retorna_none(self):
        # 30/02 não existe
        texto = (
            "do dia 30/02/2026 (08:00:00) até o dia 31/02/2026 (09:00:00)"
        )
        inicio, fim = extrair_datas(texto)
        assert inicio is None
        assert fim    is None


# ===========================================================================
# extrair_cidades
# ===========================================================================

class TestExtrairCidades:

    def test_lista_multiplas_cidades(self):
        cidades = extrair_cidades(TEXTO_NOTICIA)
        assert "Belo Horizonte" in cidades
        assert "Contagem"       in cidades
        assert "Nova Lima"      in cidades
        assert "Vespasiano"     in cidades

    def test_cidade_unica(self):
        texto = (
            "na Cidade de CAPELINHA o abastecimento poderá apresentar intermitência."
        )
        cidades = extrair_cidades(texto)
        assert cidades == ["Capelinha"]

    def test_municipios_variacao(self):
        texto = (
            "nos Municípios de BICAS E GUARARÁ o abastecimento "
            "do dia 28/06/2026 (08:00:00) até o dia 29/06/2026 (10:00:00)"
        )
        cidades = extrair_cidades(texto)
        assert "Bicas" in cidades
        assert "Guarará" in cidades

    def test_retorna_lista_vazia_sem_padrao(self):
        assert extrair_cidades("Texto sem menção de cidades.") == []

    def test_conjuncao_e_nao_vira_cidade(self):
        texto = (
            "nas Cidades de SABARÁ e SANTA LUZIA o abastecimento "
            "do dia 01/07/2026 (06:00:00) até o dia 02/07/2026 (07:00:00)"
        )
        cidades = extrair_cidades(texto)
        # "e" isolado não deve aparecer como cidade
        assert "E" not in cidades
        assert "e" not in cidades
        assert "Sabará" in cidades
        assert "Santa Luzia" in cidades


# ===========================================================================
# cruzar_bairros
# ===========================================================================

class TestCruzarBairros:

    def test_encontra_bairros_presentes(self):
        resultado = cruzar_bairros(TEXTO_NOTICIA, BAIRROS, ALIASES)
        assert set(resultado) == {"Nazare", "Sao Gabriel", "Vista do Sol"}

    def test_retorna_vazio_sem_match(self):
        texto = "Interrupção em CONTAGEM. Sem menção de bairros monitorados."
        assert cruzar_bairros(texto, BAIRROS, ALIASES) == []

    def test_alias_com_acento_mapeia_para_canonico(self):
        texto = "Bairros afetados: Nazaré e São Gabriel."
        resultado = cruzar_bairros(texto, BAIRROS, ALIASES)
        assert "Nazare"      in resultado
        assert "Sao Gabriel" in resultado

    def test_case_insensitive(self):
        texto = "bairros: NAZARE, sao gabriel, VISTA DO SOL"
        resultado = cruzar_bairros(texto, BAIRROS, ALIASES)
        assert set(resultado) == {"Nazare", "Sao Gabriel", "Vista do Sol"}

    def test_sem_duplicatas_quando_alias_e_nome_presentes(self):
        # "Nazaré" e "Nazare" no mesmo texto não devem gerar duplicata
        texto = "Nazaré (Nazare) será afetado."
        resultado = cruzar_bairros(texto, BAIRROS, ALIASES)
        assert resultado.count("Nazare") == 1

    def test_correspondencia_parcial_nao_dispara(self):
        # "Gabriel" não deve disparar "Sao Gabriel"
        texto = "Rua Gabriel Monteiro será interditada."
        resultado = cruzar_bairros(texto, BAIRROS, ALIASES)
        assert "Sao Gabriel" not in resultado

    def test_lista_bairros_vazia(self):
        assert cruzar_bairros(TEXTO_NOTICIA, [], {}) == []


# ===========================================================================
# dentro_da_janela
# ===========================================================================

class TestDentroDaJanela:

    def _titulo(self, delta_dias: int) -> str:
        """Gera título com data = hoje + delta_dias."""
        d = date.today() + timedelta(days=delta_dias)
        return f"{d.strftime('%d/%m')} - CIDADE TESTE - Situação"

    def test_hoje_esta_dentro(self):
        assert dentro_da_janela(self._titulo(0)) is True

    def test_ontem_esta_dentro(self):
        assert dentro_da_janela(self._titulo(-1)) is True

    def test_limite_exato_esta_dentro(self):
        # exatamente 14 dias atrás deve ser incluído
        assert dentro_da_janela(self._titulo(-14)) is True

    def test_alem_do_limite_esta_fora(self):
        assert dentro_da_janela(self._titulo(-15)) is False

    def test_janela_personalizada(self):
        assert dentro_da_janela(self._titulo(-7), janela_dias=7)  is True
        assert dentro_da_janela(self._titulo(-8), janela_dias=7)  is False

    def test_titulo_sem_data_fail_open(self):
        assert dentro_da_janela("Sem data - CIDADE - Situação") is True

    def test_titulo_vazio_fail_open(self):
        assert dentro_da_janela("") is True

    def test_data_futura_proxima_esta_dentro(self):
        # Amanhã ainda dentro (delta = +1, janela = 14)
        assert dentro_da_janela(self._titulo(1)) is True

    @patch("scraper.utils.datetime")
    def test_virada_de_ano(self, mock_dt):
        """Data 31/12 com hoje = 05/01 deve recuar um ano e ser reconhecida."""
        mock_dt.now.return_value = datetime(2027, 1, 5)
        mock_dt.strptime.side_effect = lambda *a, **kw: datetime.strptime(*a, **kw)
        titulo = "31/12 - CIDADE - Situação"
        # 5 dias atrás (31/12/2026 → 05/01/2027)
        resultado = dentro_da_janela(titulo, janela_dias=14)
        assert resultado is True


# ===========================================================================
# cache (funções puras de lógica)
# ===========================================================================

class TestCache:

    def test_hash_deterministico(self):
        url = "https://copasa.com.br/noticia/123"
        assert _url_hash(url) == _url_hash(url)

    def test_hashes_distintos_para_urls_distintas(self):
        assert _url_hash("https://a.com") != _url_hash("https://b.com")

    def test_nao_processado_quando_ausente(self):
        cache: dict = {}
        assert cache_ja_processado(cache, "https://x.com") is False

    def test_processado_sem_alerta_retorna_true(self):
        cache: dict = {}
        url = "https://x.com/noticia/1"
        cache_registrar(cache, url, None)          # sem alerta
        assert cache_ja_processado(cache, url) is True

    def test_processado_com_alerta_retorna_false(self):
        # Artigos com alerta devem ser sempre re-checados
        cache: dict = {}
        url = "https://x.com/noticia/2"
        cache_registrar(cache, url, {"titulo": "alerta"})
        assert cache_ja_processado(cache, url) is False

    def test_registrar_grava_campos_esperados(self):
        cache: dict = {}
        url = "https://x.com/noticia/3"
        cache_registrar(cache, url, None)
        entrada = cache[_url_hash(url)]
        assert entrada["url"]        == url
        assert entrada["teve_alerta"] is False
        assert "processado_em" in entrada

    def test_carregar_retorna_dict_vazio_sem_arquivo(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scraper.cache.CACHE_FILE", tmp_path / "inexistente.json")
        assert cache_carregar() == {}

    def test_carregar_retorna_dict_vazio_com_json_corrompido(self, tmp_path, monkeypatch):
        f = tmp_path / ".cache.json"
        f.write_text("{ INVALIDO }", encoding="utf-8")
        monkeypatch.setattr("scraper.cache.CACHE_FILE", f)
        assert cache_carregar() == {}

    def test_salvar_e_carregar_roundtrip(self, tmp_path, monkeypatch):
        f = tmp_path / ".cache.json"
        monkeypatch.setattr("scraper.cache.CACHE_FILE", f)
        cache_original = {"abc": {"url": "https://x.com", "teve_alerta": False, "processado_em": "2026-06-28T10:00:00"}}
        cache_salvar(cache_original)
        assert cache_carregar() == cache_original


# ===========================================================================
# processar_noticia — modo cidade inteira (bairros vazio)
# ===========================================================================

class TestProcessarNoticia:

    URL  = "https://copasa.com.br/noticia/teste"
    TITULO = "28/06 - BELO HORIZONTE - Situação do Abastecimento"

    def test_modo_bairro_retorna_interrupcao_com_match(self):
        resultado = processar_noticia(
            url=self.URL, titulo=self.TITULO,
            texto=TEXTO_NOTICIA,
            bairros=BAIRROS, aliases=ALIASES,
        )
        assert resultado is not None
        assert set(resultado.bairros_afetados) == {"Nazare", "Sao Gabriel", "Vista do Sol"}

    def test_modo_bairro_retorna_none_sem_match(self):
        resultado = processar_noticia(
            url=self.URL, titulo=self.TITULO,
            texto="Interrupção em CONTAGEM. Sem bairros monitorados.",
            bairros=BAIRROS, aliases=ALIASES,
        )
        assert resultado is None

    def test_modo_cidade_inteira_aceita_alerta_sem_bairros(self):
        """bairros=[] deve aceitar qualquer alerta e extrair bairros do texto."""
        resultado = processar_noticia(
            url=self.URL, titulo=self.TITULO,
            texto=TEXTO_NOTICIA,
            bairros=[], aliases={},
        )
        assert resultado is not None
        # TEXTO_NOTICIA tem "Bairros afetados: Nazare, Sao Gabriel, Vista do Sol"
        # extrair_bairros_do_texto deve capturá-los
        assert set(resultado.bairros_afetados) == {"Nazare", "Sao Gabriel", "Vista Do Sol"}

    def test_modo_cidade_inteira_aceita_texto_sem_bairro_conhecido(self):
        """Mesmo um texto sem bairros monitorados deve gerar alerta no modo cidade inteira."""
        texto = (
            "nas Cidades de BELO HORIZONTE o abastecimento poderá apresentar "
            "intermitência, do dia 01/07/2026 (08:00:00) até o dia 02/07/2026 (18:00:00)"
        )
        resultado = processar_noticia(
            url=self.URL, titulo=self.TITULO,
            texto=texto,
            bairros=[], aliases={},
        )
        assert resultado is not None
        assert resultado.bairros_afetados == []
        assert resultado.inicio == datetime(2026, 7, 1, 8, 0, 0, tzinfo=BRT)

    def test_modo_cidade_inteira_preserva_exit_code_1(self):
        """Exit code 1 é determinado pela presença de alertas, não de bairros."""
        resultado = processar_noticia(
            url=self.URL, titulo=self.TITULO,
            texto=TEXTO_NOTICIA,
            bairros=[], aliases={},
        )
        # resultado não-None → exit code 1 no orquestrador
        assert resultado is not None


# ===========================================================================
# extrair_bairros_do_texto
# ===========================================================================

TEXTO_COM_BAIRROS_AFETADOS = (
    "A Copasa informa que o abastecimento será interrompido.\n"
    "do dia 01/07/2026 (06:00:00) até o dia 02/07/2026 (12:00:00)\n"
    "\n"
    "BAIRROS AFETADOS\n"
    "Belo Horizonte: Belo Horizonte: Aarão Reis, Acaiaca, Vista Do Sol\n"
    "Contagem: Contagem: Cidade Industrial, Riacho Das Pedras\n"
)

TEXTO_COM_BAIRROS_SEM_DUPLICATA_CIDADE = (
    "Interrupção programada.\n"
    "do dia 05/07/2026 (08:00:00) até o dia 06/07/2026 (10:00:00)\n"
    "\n"
    "BAIRROS AFETADOS\n"
    "Nova Lima: Bairro Alpha, Bairro Beta, Bairro Alpha\n"
)


class TestExtrairBairrosDoTexto:

    def test_retorna_vazio_sem_secao_bairros(self):
        assert extrair_bairros_do_texto("Texto sem seção de bairros.") == []

    def test_extrai_bairros_de_multiplas_cidades(self):
        resultado = extrair_bairros_do_texto(TEXTO_COM_BAIRROS_AFETADOS)
        assert "Aarão Reis" in resultado
        assert "Acaiaca" in resultado
        assert "Vista Do Sol" in resultado
        assert "Cidade Industrial" in resultado
        assert "Riacho Das Pedras" in resultado

    def test_remove_prefixo_duplicado_da_cidade(self):
        """Formato "Cidade: Cidade: bairro1, ..." não deve gerar "Cidade" como bairro."""
        resultado = extrair_bairros_do_texto(TEXTO_COM_BAIRROS_AFETADOS)
        assert "Belo Horizonte" not in resultado
        assert "Contagem" not in resultado

    def test_sem_duplicatas_quando_bairro_repetido(self):
        resultado = extrair_bairros_do_texto(TEXTO_COM_BAIRROS_SEM_DUPLICATA_CIDADE)
        assert resultado.count("Bairro Alpha") == 1

    def test_titulo_case_aplicado(self):
        texto = (
            "BAIRROS AFETADOS\n"
            "Sabará: Sabará: centro velho, NOVA ESPERANÇA\n"
        )
        resultado = extrair_bairros_do_texto(texto)
        assert "Centro Velho" in resultado
        assert "Nova Esperança" in resultado

    def test_retorna_lista_vazia_para_secao_vazia(self):
        texto = "BAIRROS AFETADOS\n"
        assert extrair_bairros_do_texto(texto) == []

    def test_processar_noticia_cidade_inteira_popula_bairros_afetados(self):
        """processar_noticia com bairros=[] deve chamar extrair_bairros_do_texto."""
        resultado = processar_noticia(
            url="https://copasa.com.br/teste",
            titulo="01/07 - BELO HORIZONTE - Situação do Abastecimento",
            texto=TEXTO_COM_BAIRROS_AFETADOS,
            bairros=[],
            aliases={},
        )
        assert resultado is not None
        assert "Aarão Reis" in resultado.bairros_afetados
        assert "Cidade Industrial" in resultado.bairros_afetados

    # --- Testes de exemplo para Formato 2 ---

    def test_formato2_lista_plana_simples(self):
        """Formato 2: lista plana com bairros separados por vírgula."""
        texto = (
            "BAIRROS AFETADOS\n"
            "Aarão Reis, Acaiaca, Nazaré\n"
        )
        resultado = extrair_bairros_do_texto(texto)
        assert "Aarão Reis" in resultado
        assert "Acaiaca" in resultado
        assert "Nazaré" in resultado
        assert len(resultado) == 3

    def test_formato2_e_como_separador(self):
        """Formato 2: último item usa ' e ' como separador — deve retornar dois bairros."""
        texto = (
            "BAIRROS AFETADOS\n"
            "Riacho Das Pedras, Vila Cemig e Vila Esperança\n"
        )
        resultado = extrair_bairros_do_texto(texto)
        assert "Vila Cemig" in resultado
        assert "Vila Esperança" in resultado
        assert "Riacho Das Pedras" in resultado

    def test_formato2_unico_bairro(self):
        """Formato 2: apenas um bairro na lista plana."""
        texto = (
            "BAIRROS AFETADOS\n"
            "Centro\n"
        )
        resultado = extrair_bairros_do_texto(texto)
        assert resultado == ["Centro"]

    def test_warning_emitido_quando_sem_bairros(self, caplog):
        """Cabeçalho presente mas sem bairros → WARNING deve ser emitido."""
        texto = "BAIRROS AFETADOS\n"
        with caplog.at_level(logging.WARNING, logger="scraper"):
            resultado = extrair_bairros_do_texto(texto)
        assert resultado == []
        assert any("cabeçalho encontrado" in msg.lower() or "bairros afetados" in msg.lower()
                   for msg in caplog.messages)


# ===========================================================================
# Property-based tests (Hypothesis) — extrair_bairros_do_texto
# ===========================================================================

# Strategy for generating valid bairro names: printable text, no commas, no
# leading/trailing whitespace, non-empty, and not containing " e " to avoid
# ambiguous splitting.
_bairro_name = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Lt", "Lm", "Lo", "Nd", "Zs"),
        whitelist_characters="ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ",
    ),
    min_size=2,
    max_size=40,
).map(str.strip).filter(
    lambda s: (
        s
        and len(s) >= 2
        and "," not in s
        and " e " not in s
        and s.strip().lower() != "e"
        and ":" not in s
        and "\n" not in s
    )
)


def _build_formato2_text(nomes: list[str]) -> str:
    """Monta um texto no Formato 2 a partir de uma lista de nomes de bairros."""
    if len(nomes) == 1:
        lista = nomes[0]
    else:
        # Junta com ", " e usa " e " antes do último item
        lista = ", ".join(nomes[:-1]) + " e " + nomes[-1]
    return f"BAIRROS AFETADOS\n{lista}\n"


def _build_formato1_text(cidades_bairros: list[tuple[str, list[str]]]) -> str:
    """Monta um texto no Formato 1 a partir de pares (cidade, [bairros])."""
    lines = ["BAIRROS AFETADOS"]
    for cidade, bairros in cidades_bairros:
        linha = f"{cidade}: {', '.join(bairros)}"
        lines.append(linha)
    return "\n".join(lines) + "\n"


# Feature: bairros-afetados-extraction-fix, Property 1: Formato 2 extrai todos os bairros da lista plana
class TestPropertyFormato2ExtraiTodos:

    @given(st.lists(_bairro_name, min_size=1, max_size=10))
    @settings(max_examples=100)
    def test_property_formato2_extrai_todos(self, nomes):
        # Validates: Requirements 2.2, 2.3
        # Deduplicate while preserving order to match function behaviour
        seen = set()
        nomes_unicos = []
        for n in nomes:
            title = n.title()
            if title not in seen:
                seen.add(title)
                nomes_unicos.append(n)
        assume(len(nomes_unicos) >= 1)

        texto = _build_formato2_text(nomes_unicos)
        resultado = extrair_bairros_do_texto(texto)

        for nome in nomes_unicos:
            assert nome.title() in resultado, (
                f"Bairro '{nome.title()}' não encontrado em {resultado!r}\n"
                f"Texto: {texto!r}"
            )


# Feature: bairros-afetados-extraction-fix, Property 2: Invariante de saída
class TestPropertyInvarianteSaida:

    @given(st.lists(_bairro_name, min_size=1, max_size=10))
    @settings(max_examples=100)
    def test_property_invariante_saida_formato2(self, nomes):
        # Validates: Requirements 2.5, 3.5
        texto = _build_formato2_text(nomes)
        resultado = extrair_bairros_do_texto(texto)

        for elem in resultado:
            assert elem == elem.strip(), f"Elemento com espaços extras: {elem!r}"
            assert elem != "", "Elemento vazio encontrado"

        assert len(resultado) == len(set(resultado)), (
            f"Duplicatas encontradas: {resultado}"
        )

    @given(st.lists(_bairro_name, min_size=1, max_size=10))
    @settings(max_examples=100)
    def test_property_invariante_saida_formato1(self, nomes):
        # Validates: Requirements 2.5, 3.5
        # Build a simple Formato 1 text with one city
        cidade = "Contagem"
        texto = _build_formato1_text([(cidade, nomes)])
        resultado = extrair_bairros_do_texto(texto)

        for elem in resultado:
            assert elem == elem.strip(), f"Elemento com espaços extras: {elem!r}"
            assert elem != "", "Elemento vazio encontrado"

        assert len(resultado) == len(set(resultado)), (
            f"Duplicatas encontradas: {resultado}"
        )


# Feature: bairros-afetados-extraction-fix, Property 3: Regressão Formato 1
class TestPropertyRegressaoFormato1:

    @given(st.lists(_bairro_name, min_size=1, max_size=10))
    @settings(max_examples=100)
    def test_property_regressao_formato1(self, nomes):
        # Validates: Requirements 3.1, 3.2, 3.4
        cidade = "Belo Horizonte"
        # Use "Cidade: Cidade: bairros" pattern (duplicated prefix as seen in real data)
        linha = f"{cidade}: {cidade}: {', '.join(nomes)}"
        texto = f"BAIRROS AFETADOS\n{linha}\n"
        resultado = extrair_bairros_do_texto(texto)

        # All bairros should appear in the result
        for nome in nomes:
            assert nome.title() in resultado, (
                f"Bairro '{nome.title()}' não encontrado em {resultado!r}\n"
                f"Texto: {texto!r}"
            )

        # City name should NOT appear as a bairro
        assert cidade not in resultado, (
            f"Nome da cidade '{cidade}' não deve aparecer como bairro: {resultado!r}"
        )
        assert cidade.upper() not in [b.upper() for b in resultado], (
            f"Nome da cidade '{cidade}' (qualquer case) não deve aparecer como bairro: {resultado!r}"
        )


# ===========================================================================
# _filtrar_encerrados
# ===========================================================================

def _make_interrupcao(inicio: datetime, fim: datetime) -> Interrupcao:
    return Interrupcao(
        titulo="Teste",
        url="http://example.com",
        cidades=["Belo Horizonte"],
        inicio=inicio,
        fim=fim,
        bairros_afetados=["Nazare"],
        texto_bruto="",
    )


class TestFiltrarEncerrados:

    def test_ativo_sempre_passa(self):
        agora = datetime.now(BRT)
        it = _make_interrupcao(agora - timedelta(hours=1), agora + timedelta(hours=2))
        assert _filtrar_encerrados([it]) == [it]

    def test_encerrado_hoje_passa(self):
        agora = datetime.now(BRT)
        it = _make_interrupcao(agora - timedelta(hours=4), agora - timedelta(minutes=30))
        assert _filtrar_encerrados([it]) == [it]

    def test_encerrado_ontem_passa(self):
        agora = datetime.now(BRT)
        fim_ontem = agora.replace(hour=10, minute=0) - timedelta(days=1)
        it = _make_interrupcao(fim_ontem - timedelta(hours=2), fim_ontem)
        assert _filtrar_encerrados([it]) == [it]

    def test_encerrado_anteontem_removido(self):
        agora = datetime.now(BRT)
        fim = agora - timedelta(days=2)
        it = _make_interrupcao(fim - timedelta(hours=2), fim)
        assert _filtrar_encerrados([it]) == []

    def test_sem_data_fim_sempre_passa(self):
        agora = datetime.now(BRT)
        it = _make_interrupcao(agora - timedelta(hours=1), agora + timedelta(hours=1))
        it.fim = None
        assert _filtrar_encerrados([it]) == [it]

    def test_mistura_ativos_e_antigos(self):
        agora = datetime.now(BRT)
        ativo = _make_interrupcao(agora - timedelta(hours=1), agora + timedelta(hours=2))
        antigo = _make_interrupcao(agora - timedelta(days=5), agora - timedelta(days=2))
        assert _filtrar_encerrados([ativo, antigo]) == [ativo]


# Feature: bairros-afetados-extraction-fix, Property 4: Sem cabeçalho → lista vazia
class TestPropertySemCabecalhoRetornaVazio:

    @given(st.text())
    @settings(max_examples=100)
    def test_property_sem_cabecalho_retorna_vazio(self, texto):
        # Validates: Requirements 3.3
        assume("BAIRROS AFETADOS" not in texto.upper())
        resultado = extrair_bairros_do_texto(texto)
        assert resultado == [], (
            f"Esperado [] para texto sem cabeçalho, obtido {resultado!r}"
        )
