# Bugfix Requirements Document

## Introduction

A função `extrair_bairros_do_texto` em `scraper.py` retorna lista vazia `[]` para notícias que usam o Formato 2 do HTML da Copasa. O bug ocorre porque a implementação atual baseia-se exclusivamente na regex `BAIRROS_CIDADE_PATTERN`, que só detecta parágrafos com `NomeDaCidade: bairros...`. No Formato 2, o cabeçalho "BAIRROS AFETADOS" usa `<strong>` e os bairros aparecem em um único parágrafo plano sem prefixo de cidade, fazendo com que o padrão não produza nenhum match e a lista retorne vazia.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN o cabeçalho "BAIRROS AFETADOS" aparece com o texto dentro de `<strong>` (ex: `<p ...><strong>BAIRROS AFETADOS</strong></p>`) THEN o sistema não reconhece o cabeçalho e retorna lista vazia, ignorando completamente a seção de bairros

1.2 WHEN os bairros estão listados em um único `<p>` plano separados por vírgula e `" e "`, sem prefixo `NomeDaCidade:` (Formato 2) THEN o sistema retorna lista vazia pois `BAIRROS_CIDADE_PATTERN` não encontra nenhum match

1.3 WHEN o último item da lista de bairros é precedido por `" e "` em vez de vírgula (ex: `"Vila Cemig e Vila Esperança"`) THEN o sistema trata a expressão inteira como um único bairro em vez de dois bairros separados

### Expected Behavior (Correct)

2.1 WHEN o cabeçalho "BAIRROS AFETADOS" aparece com o texto dentro de `<strong>` THEN o sistema SHALL reconhecer o cabeçalho e prosseguir com a extração dos bairros nos parágrafos seguintes

2.2 WHEN os bairros estão listados em um único `<p>` plano separados por vírgula e sem prefixo de cidade (Formato 2) THEN o sistema SHALL extrair cada item separado por vírgula como um bairro individual

2.3 WHEN o último item da lista de bairros contém `" e "` separando dois nomes (ex: `"Vila Cemig e Vila Esperança"`) THEN o sistema SHALL separar os dois nomes e retorná-los como bairros distintos

2.4 WHEN nenhum bairro é encontrado após o cabeçalho "BAIRROS AFETADOS" THEN o sistema SHALL emitir um log de warning para facilitar o diagnóstico de novos formatos

2.5 WHEN bairros são extraídos em qualquer formato THEN o sistema SHALL aplicar `.strip()` em cada nome, remover entradas vazias e garantir ausência de duplicatas na lista final

### Unchanged Behavior (Regression Prevention)

3.1 WHEN o cabeçalho "BAIRROS AFETADOS" aparece como texto puro sem `<strong>` (Formato 1) THEN o sistema SHALL CONTINUE TO reconhecer o cabeçalho corretamente

3.2 WHEN os bairros estão estruturados com prefixo `NomeDaCidade: bairro1, bairro2, ...` por parágrafo (Formato 1) THEN o sistema SHALL CONTINUE TO extrair todos os bairros removendo o prefixo da cidade

3.3 WHEN o texto não contém a seção "BAIRROS AFETADOS" THEN o sistema SHALL CONTINUE TO retornar lista vazia

3.4 WHEN o nome de uma cidade aparece duplicado no prefixo (ex: `"Belo Horizonte: Belo Horizonte: Aarão Reis, ..."`) THEN o sistema SHALL CONTINUE TO não incluir o nome da cidade como bairro na lista final

3.5 WHEN um bairro aparece mais de uma vez no conteúdo extraído THEN o sistema SHALL CONTINUE TO retornar cada bairro apenas uma vez na lista final
