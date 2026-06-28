# Design Document: bairros-afetados-extraction-fix

## Overview

A função `extrair_bairros_do_texto` em `scraper.py` não consegue extrair bairros de notícias no
Formato 2 do portal Copasa. O fix é cirúrgico: ampliar a função para suportar dois formatos de
estruturação da seção "BAIRROS AFETADOS", mantendo compatibilidade total com o Formato 1 já
funcionando.

### Os dois formatos

**Formato 1 — prefixo por cidade** (já funciona):
```
BAIRROS AFETADOS
Belo Horizonte: Belo Horizonte: Aarão Reis, Acaiaca, Vista Do Sol
Contagem: Contagem: Cidade Industrial, Riacho Das Pedras
```

**Formato 2 — parágrafo plano** (bug atual):
```
BAIRROS AFETADOS
Aarão Reis, Acaiaca, Nazaré, Riacho Das Pedras, Vila Cemig e Vila Esperança
```

No Formato 2, o Playwright entrega o texto já como string plana (o `<strong>` do HTML é
descartado na conversão para texto). A diferença real está na ausência do padrão `NomeDaCidade: ...`
e no uso de `" e "` como separador antes do último item.

---

## Architecture

A correção é inteiramente contida em `scraper.py`. Nenhum novo módulo é necessário.

```
scraper.py
└── extrair_bairros_do_texto(texto: str) -> list[str]   ← único ponto de mudança
```

A função já recebe o texto como string plana (após `inner_text()` do Playwright), portanto
nenhuma mudança é necessária nos pontos de chamada (`processar_noticia`) nem na navegação.

---

## Components and Interfaces

### `extrair_bairros_do_texto` (refatorada)

**Assinatura permanece igual:**
```python
def extrair_bairros_do_texto(texto: str) -> list[str]
```

**Lógica ampliada:**

1. Procurar pelo cabeçalho `BAIRROS_AFETADOS_HEADER` (regex existente, já case-insensitive e
   tolerante a espaços extras — nenhuma mudança necessária aqui).
2. Isolar o trecho a partir do cabeçalho (idem ao código atual).
3. **Tentar Formato 1**: executar `BAIRROS_CIDADE_PATTERN.finditer(trecho)`. Se produzir pelo
   menos um match, processar como hoje (remover prefixo de cidade duplicado, split por vírgula,
   Title Case, deduplicar).
4. **Fallback para Formato 2**: se nenhum match de Formato 1 for encontrado, tomar a primeira
   linha não-vazia após o cabeçalho como a lista plana de bairros. Dividir por vírgula, tratar
   o último fragmento com split por `" e "`, aplicar `.strip()`, Title Case e deduplicar.
5. Se após ambos os caminhos a lista ainda estiver vazia, emitir `log.warning(...)`.

### Separador `" e "`

O split por `" e "` é aplicado **somente no último fragmento** de cada linha de Formato 2 — não
em toda a string — para evitar falsos positivos com bairros cujos nomes contêm "e" (ex: "Padre
Eustáquio").

Implementação:
```python
fragmentos = linha.split(",")
bairros_raw = []
for i, frag in enumerate(fragmentos):
    if i == len(fragmentos) - 1 and " e " in frag:
        bairros_raw.extend(frag.split(" e "))
    else:
        bairros_raw.append(frag)
```

---

## Data Models

Nenhuma mudança nos modelos de dados. `Interrupcao` e `processar_noticia` permanecem intactos.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a
system — essentially, a formal statement about what the system should do. Properties serve as the
bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Formato 2 extrai todos os bairros da lista plana

*Para qualquer* lista não-vazia de N nomes de bairros formatada como parágrafo plano separado
por vírgulas (e opcionalmente `" e "` antes do último item), após o cabeçalho "BAIRROS AFETADOS",
`extrair_bairros_do_texto` SHALL retornar exatamente aqueles N bairros (em Title Case).

**Validates: Requirements 2.2, 2.3**

### Property 2: Invariante de saída — sem espaços, sem vazios, sem duplicatas

*Para qualquer* texto de entrada (Formato 1 ou Formato 2), todos os elementos retornados por
`extrair_bairros_do_texto` SHALL ter `.strip()` aplicado, nenhum elemento SHALL ser string vazia,
e nenhum elemento SHALL aparecer mais de uma vez na lista final.

**Validates: Requirements 2.5, 3.5**

### Property 3: Regressão Formato 1 — city-prefix continua funcionando

*Para qualquer* texto com a seção "BAIRROS AFETADOS" no Formato 1 (linhas `NomeCidade: bairro1,
bairro2, ...`), `extrair_bairros_do_texto` SHALL retornar todos os bairros listados e SHALL NOT
incluir nenhum nome de cidade como bairro.

**Validates: Requirements 3.1, 3.2, 3.4**

### Property 4: Sem cabeçalho → lista vazia

*Para qualquer* texto que não contenha a string "BAIRROS AFETADOS" (case-insensitive),
`extrair_bairros_do_texto` SHALL retornar `[]`.

**Validates: Requirements 3.3**

---

## Error Handling

| Situação                                          | Comportamento                               |
| ------------------------------------------------- | ------------------------------------------- |
| Cabeçalho presente, sem bairros em nenhum formato | `log.warning(...)` + retorna `[]`           |
| Texto vazio                                       | Regex não encontra cabeçalho → retorna `[]` |
| Linha com vírgulas extras (`"a, , b"`)            | `.strip()` + filtro de strings vazias       |

---

## Testing Strategy

### Abordagem dual

- **Testes de exemplo** (pytest): cobrem casos concretos do bugfix e regressões do Formato 1.
  Já existem em `tests/test_scraper.py` — os novos testes são adicionados à classe
  `TestExtrairBairrosDoTexto` existente.
- **Testes de propriedade** (Hypothesis): validam as quatro propriedades formais acima para uma
  grande variedade de entradas geradas automaticamente.

### Biblioteca de property-based testing

**Hypothesis** (`hypothesis>=6.0`) — padrão de fato para Python, integra nativamente com pytest.

### Configuração mínima

Cada teste de propriedade executa **mínimo de 100 iterações** (padrão do Hypothesis é 100;
nenhum ajuste necessário).

Cada teste de propriedade inclui um comentário de rastreabilidade:
```python
# Feature: bairros-afetados-extraction-fix, Property N: <texto da propriedade>
```

### Testes de exemplo a adicionar

1. `test_formato2_lista_plana_simples` — texto Formato 2 com 3 bairros separados por vírgula.
2. `test_formato2_e_como_separador` — último item com `" e "` (ex: "Vila Cemig e Vila Esperança").
3. `test_formato2_unico_bairro` — Formato 2 com apenas um bairro.
4. `test_warning_emitido_quando_sem_bairros` — cabeçalho presente, lista vazia → `caplog` verifica WARNING.

### Testes de propriedade a adicionar

- **`test_property_formato2_extrai_todos`** → Property 1
- **`test_property_invariante_saida`** → Property 2
- **`test_property_regressao_formato1`** → Property 3
- **`test_property_sem_cabecalho_retorna_vazio`** → Property 4

### Testes de regressão existentes

Os testes já presentes em `TestExtrairBairrosDoTexto` e `TestProcessarNoticia` devem continuar
passando sem modificação — servem como proteção de regressão automática.
