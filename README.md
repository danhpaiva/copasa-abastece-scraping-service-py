# copasa-abastece-scraping-service-py

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-1.49-green?logo=playwright&logoColor=white)
![pytest](https://img.shields.io/badge/pytest-37%20testes-brightgreen?logo=pytest&logoColor=white)
![License](https://img.shields.io/github/license/danhpaiva/copasa-abastece-scraping-service-py)
![Branch](https://img.shields.io/badge/branch-develop-orange)

Monitora o portal de notícias da [Copasa](https://www.copasa.com.br/wps/portal/internet/imprensa/noticias/informacoes-sobre-abastecimento) em busca de interrupções no abastecimento de água nos bairros configurados.

---

## Como funciona

```
1. Carrega a listagem de notícias (1 request)
        │
        ├─ Filtra por janela de datas (padrão: 14 dias)
        └─ Filtra por cidade-alvo no resumo  →  candidatos
                │
                └─ Navega para cada candidato
                        └─ Cruza texto completo com lista de bairros
                                └─ Deduplica por (início, fim, cidades)
                                        └─ Exibe alerta ou JSON
```

O resumo de cada notícia já está disponível na listagem do portal IBM WCM, então a navegação individual só ocorre para artigos que passam nos dois filtros — reduzindo requisições e exposição ao WAF.

---

## Requisitos

- Python 3.10+
- Chromium (instalado automaticamente pelo Playwright)

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## Configuração

Edite o arquivo `bairros.json` para definir o que monitorar:

```json
{
  "bairros": [
    "Nazare",
    "Sao Gabriel",
    "Vista do Sol"
  ],
  "aliases": {
    "Nazaré": "Nazare",
    "São Gabriel": "Sao Gabriel"
  },
  "cidades_alvo": [
    "Belo Horizonte"
  ]
}
```

| Campo | Descrição |
|---|---|
| `bairros` | Lista de bairros a monitorar (sem acento — normalização é automática) |
| `aliases` | Variações com acento ou grafia alternativa mapeadas ao nome canônico |
| `cidades_alvo` | Pré-filtro por cidade na listagem — reduz navegações desnecessárias |

---

## Uso

### Varredura completa (modo padrão)

```bash
python scraper.py
```

### Saída em JSON (para integrações)

Logs de diagnóstico vão para `stderr`; o JSON vai para `stdout`.

```bash
python scraper.py --json
python scraper.py --json 2>/dev/null   # só o JSON
```

Exemplo de saída:

```json
[
  {
    "titulo": "28/06 - BELO HORIZONTE - Situação do Abastecimento",
    "url": "https://www.copasa.com.br/...",
    "cidades": ["Belo Horizonte", "Contagem"],
    "inicio": "2026-06-28T06:00:00",
    "fim": "2026-06-30T07:00:00",
    "esta_ativa": true,
    "bairros_afetados": ["Nazare", "Sao Gabriel", "Vista do Sol"]
  }
]
```

### URL direta

Processa uma notícia específica sem percorrer a listagem:

```bash
python scraper.py "https://www.copasa.com.br/wps/portal/..."
python scraper.py "https://www.copasa.com.br/wps/portal/..." --json
```

---

## Argumentos CLI

| Argumento | Padrão | Descrição |
|---|---|---|
| `url` | — | URL direta de uma notícia (opcional) |
| `--json` | `false` | Emite resultado em JSON para stdout |
| `--janela DIAS` | `14` | Janela de dias para filtrar notícias pelo título |
| `--no-cache` | `false` | Ignora o cache e reprocessa todos os artigos |
| `--timeout SEG` | `180` | Timeout global da sessão em segundos |
| `--debug` | `false` | Habilita logs de nível DEBUG |

---

## Exit codes

| Código | Significado |
|---|---|
| `0` | Nenhuma interrupção encontrada nos bairros monitorados |
| `1` | Uma ou mais interrupções encontradas |
| `2` | Erro de execução (rede, parsing, timeout) |

Útil para uso em scripts shell, `cron` e GitHub Actions:

```bash
python scraper.py --json
if [ $? -eq 1 ]; then
  echo "Alerta ativo — acionar notificação"
fi
```

---

## Cache

Na segunda execução, artigos sem alerta são pulados automaticamente via `.cache.json` (hash SHA-1 por URL). Artigos com alerta são sempre re-checados para verificar se ainda estão ativos.

O arquivo de cache é ignorado pelo git (`.gitignore`).

```bash
# Forçar reprocessamento completo
python scraper.py --no-cache
```

---

## Estrutura do projeto

```
copasa-abastece-scraping-service-py/
├── scraper.py          # script principal
├── bairros.json        # configuração de bairros, aliases e cidades-alvo
├── requirements.txt    # dependências Python
├── tests/
│   └── test_scraper.py # testes unitários (37 casos, sem Playwright)
└── .cache.json         # cache de execução (gerado em runtime, ignorado pelo git)
```

---

## Testes

Os testes cobrem as funções puras do scraper e não dependem de Playwright ou rede. Executam em menos de 1 segundo.

```bash
pytest tests/ -v
```

Cobertura:

| Função | Cenários testados |
|---|---|
| `extrair_datas` | formato completo, hora sem segundos, texto sem data, data inválida, variações de "até/ate" |
| `extrair_cidades` | múltiplas cidades, cidade única, variação "Municípios de", conjunção "e" isolada |
| `cruzar_bairros` | match exato, alias com acento, case-insensitive, sem duplicatas, correspondência parcial |
| `dentro_da_janela` | hoje, limite exato, além do limite, janela customizada, fail-open, virada de ano |
| `cache` | hash determinístico, artigo sem alerta, artigo com alerta, roundtrip salvar/carregar, JSON corrompido |
