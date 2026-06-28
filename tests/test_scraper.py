"""
Testes unitários para as funções puras do scraper.
Não dependem de Playwright nem de rede.

Execução:
    pytest tests/ -v
"""

from datetime import datetime, date, timedelta
from unittest.mock import patch

import pytest

from scraper import (
    extrair_datas,
    extrair_cidades,
    cruzar_bairros,
    dentro_da_janela,
    processar_noticia,
    cache_carregar,
    cache_ja_processado,
    cache_registrar,
    cache_salvar,
    _url_hash,
)


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
        assert inicio == datetime(2026, 6, 28, 6, 0, 0)
        assert fim    == datetime(2026, 6, 30, 7, 0, 0)

    def test_extrai_hora_sem_segundos(self):
        texto = (
            "do dia 10/07/2026 (08:00) até o dia 11/07/2026 (18:30)"
        )
        inicio, fim = extrair_datas(texto)
        assert inicio == datetime(2026, 7, 10, 8, 0, 0)
        assert fim    == datetime(2026, 7, 11, 18, 30, 0)

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

    @patch("scraper.datetime")
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
        monkeypatch.setattr("scraper.CACHE_FILE", tmp_path / "inexistente.json")
        assert cache_carregar() == {}

    def test_carregar_retorna_dict_vazio_com_json_corrompido(self, tmp_path, monkeypatch):
        f = tmp_path / ".cache.json"
        f.write_text("{ INVALIDO }", encoding="utf-8")
        monkeypatch.setattr("scraper.CACHE_FILE", f)
        assert cache_carregar() == {}

    def test_salvar_e_carregar_roundtrip(self, tmp_path, monkeypatch):
        f = tmp_path / ".cache.json"
        monkeypatch.setattr("scraper.CACHE_FILE", f)
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
        """bairros=[] deve aceitar qualquer alerta da cidade configurada."""
        resultado = processar_noticia(
            url=self.URL, titulo=self.TITULO,
            texto=TEXTO_NOTICIA,
            bairros=[], aliases={},
        )
        assert resultado is not None
        assert resultado.bairros_afetados == []

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
        assert resultado.inicio == datetime(2026, 7, 1, 8, 0, 0)

    def test_modo_cidade_inteira_preserva_exit_code_1(self):
        """Exit code 1 é determinado pela presença de alertas, não de bairros."""
        resultado = processar_noticia(
            url=self.URL, titulo=self.TITULO,
            texto=TEXTO_NOTICIA,
            bairros=[], aliases={},
        )
        # resultado não-None → exit code 1 no orquestrador
        assert resultado is not None
