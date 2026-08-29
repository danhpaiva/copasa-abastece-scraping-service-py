# copasa-abastece-scraping-service-py

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Requests](https://img.shields.io/badge/Requests-2.32-green?logo=python&logoColor=white)
![pytest](https://img.shields.io/badge/pytest-64%20testes-brightgreen?logo=pytest&logoColor=white)
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
                                        └─ Remove encerrados com mais de 1 dia (D-1)
                                                └─ Exibe alerta ou JSON
```

O resumo de cada notícia já está disponível na listagem do portal IBM WCM, então a navegação individual só ocorre para artigos que passam nos dois filtros — reduzindo requisições e exposição ao WAF.

---

## Requisitos

- Python 3.10+

```bash
pip install -r requirements.txt
```

O portal da Copasa é renderizado no servidor (IBM WebSphere Portal), então a coleta usa `requests` + `BeautifulSoup` — sem necessidade de browser headless. Isso também evita bloqueios de WAF que costumam mirar o fingerprint TLS de browsers automatizados.

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

| Campo          | Descrição                                                                                                                                                                                    |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bairros`      | Lista de bairros a monitorar (sem acento — normalização é automática). **Lista vazia** ativa o modo cidade inteira: qualquer alerta das cidades configuradas é aceito, sem filtro por bairro |
| `aliases`      | Variações com acento ou grafia alternativa mapeadas ao nome canônico                                                                                                                         |
| `cidades_alvo` | Pré-filtro por cidade na listagem — reduz navegações desnecessárias                                                                                                                          |

> **Modo cidade inteira:** configure `"bairros": []` para receber todos os alertas de uma cidade, sem destacar bairros específicos. Útil para monitorar municípios menores onde a Copasa não detalha bairros no texto.

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
{
  "gerado_em": "2026-06-28T10:20:42",
  "total_alertas": 1,
  "alertas": [
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
}
```

### URL direta

Processa uma notícia específica sem percorrer a listagem:

```bash
python scraper.py "https://www.copasa.com.br/wps/portal/..."
python scraper.py "https://www.copasa.com.br/wps/portal/..." --json
```

---

## Argumentos CLI

| Argumento          | Padrão  | Descrição                                                          |
| ------------------ | ------- | ------------------------------------------------------------------ |
| `url`              | —       | URL direta de uma notícia (opcional)                               |
| `--json`           | `false` | Emite resultado em JSON para stdout                                |
| `--janela DIAS`    | `14`    | Janela de dias para filtrar notícias pelo título                   |
| `--output ARQUIVO` | —       | Grava o resultado em JSON no arquivo informado (ex: `alerts.json`) |
| `--no-cache`       | `false` | Ignora o cache e reprocessa todos os artigos                       |
| `--timeout SEG`    | `180`   | Timeout global da sessão em segundos                               |
| `--debug`          | `false` | Habilita logs de nível DEBUG                                       |

---

## Exit codes

| Código | Significado                                            |
| ------ | ------------------------------------------------------ |
| `0`    | Nenhuma interrupção encontrada nos bairros monitorados |
| `1`    | Uma ou mais interrupções encontradas                   |
| `2`    | Erro de execução (rede, parsing, timeout)              |

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

## Política de retenção de alertas encerrados

Alertas com status **ENCERRADO** (data de fim no passado) são exibidos apenas se encerraram no dia atual ou no dia anterior (D-1). Alertas mais antigos são descartados automaticamente.

Alertas **ATIVOS** e alertas sem data de fim identificada passam sempre, independentemente da data.

| Status      | Regra de retenção                        |
| ----------- | ---------------------------------------- |
| Ativo       | Sempre exibido                           |
| Encerrado   | Exibido apenas se `data_fim >= hoje - 1` |
| Sem data    | Sempre exibido (fail-open)               |

> Esta lógica é aplicada exclusivamente no scraper. O modo `--url` (URL direta) não aplica o filtro, pois é voltado para diagnóstico manual.

---

## Estrutura do projeto

```
copasa-abastece-scraping-service-py/
├── scraper.py              # entry point (python scraper.py)
├── scraper/
│   ├── config.py           # constantes, paths, regexes, user agents
│   ├── models.py           # dataclass Interrupcao
│   ├── utils.py            # normalizar, carregar_bairros, parse_datetime, dentro_da_janela
│   ├── cache.py            # cache de execução por hash SHA-1
│   ├── timeout.py          # context manager de timeout global
│   ├── browser.py          # requests/BeautifulSoup: sessão, navegação, extração de links/texto
│   ├── parser.py           # extração de datas, cidades, bairros, processar_noticia
│   ├── output.py           # formatação e exibição de resultados
│   ├── monitor.py          # orquestrador: monitorar, monitorar_url_direta
│   └── cli.py              # argparse + entrypoint main()
├── bairros.json            # configuração de bairros, aliases e cidades-alvo
├── requirements.txt        # dependências Python
├── tests/
│   └── test_scraper.py     # testes unitários (64 casos, sem rede)
└── .cache.json             # cache de execução (gerado em runtime, ignorado pelo git)
```

---

## Testes

Os testes cobrem as funções puras do scraper e não dependem de rede. Executam em menos de 1 segundo.

```bash
pytest tests/ -v
```

Cobertura:

| Função                     | Cenários testados                                                                                                       |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `extrair_datas`            | formato completo, hora sem segundos, texto sem data, data inválida, variações de "até/ate"                              |
| `extrair_cidades`          | múltiplas cidades, cidade única, variação "Municípios de", conjunção "e" isolada                                        |
| `cruzar_bairros`           | match exato, alias com acento, case-insensitive, sem duplicatas, correspondência parcial                                |
| `dentro_da_janela`         | hoje, limite exato, além do limite, janela customizada, fail-open, virada de ano                                        |
| `cache`                    | hash determinístico, artigo sem alerta, artigo com alerta, roundtrip salvar/carregar, JSON corrompido                   |
| `extrair_bairros_do_texto` | sem seção BAIRROS AFETADOS, múltiplas cidades, remoção de prefixo duplicado, deduplicação, title case, seção vazia      |
| `processar_noticia`        | modo bairro com match, modo bairro sem match, modo cidade inteira com e sem bairros conhecidos, popula bairros_afetados |
| `_filtrar_encerrados`      | ativo sempre passa, encerrado hoje passa, encerrado ontem passa, encerrado anteontem removido, sem data passa, mistura  |

## Aviso legal

> ⚠ **Este não é um serviço oficial da Copasa.**
>
> Este projeto é independente, sem fins lucrativos e sem vínculo com a [Copasa](https://www.copasa.com.br). Os dados são obtidos automaticamente do site oficial da Copasa e podem apresentar atraso de até 1 hora ou inconsistências. Não nos responsabilizamos por decisões tomadas com base nas informações exibidas. Consulte sempre os canais oficiais da Copasa para informações definitivas.