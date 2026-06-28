# Implementation Plan: bairros-afetados-extraction-fix

## Overview

Fix `extrair_bairros_do_texto` in `scraper.py` to handle Formato 2 (flat paragraph, no city
prefix), split on `" e "` before the last item, and emit a warning when no bairros are found
after the header. Add Hypothesis property tests and example tests to prevent regressions.

## Tasks

- [x] 1. Add Hypothesis to the project dependencies
  - Add `hypothesis>=6.112` to `requirements.txt`
  - _Requirements: 2.2, 2.3, 2.5_

- [x] 2. Refactor `extrair_bairros_do_texto` to support Formato 2
  - Open `scraper.py` and locate `extrair_bairros_do_texto`
  - After isolating the trecho from the header, try `BAIRROS_CIDADE_PATTERN.finditer(trecho)` first (Formato 1 path — keep existing logic unchanged)
  - If Formato 1 produces zero matches, take the first non-empty line after the header as the flat bairros list (Formato 2 fallback)
  - Split the flat line by comma; for the **last fragment only**, additionally split on `" e "` to separate the final two entries
  - Apply `.strip()` to each fragment, filter empty strings, apply `.title()`, and deduplicate (preserving existing deduplication logic)
  - After both paths, if `bairros` list is still empty emit `log.warning("BAIRROS AFETADOS: cabeçalho encontrado mas nenhum bairro extraído — possível novo formato.")`
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

- [x] 3. Add example tests for Formato 2 to `TestExtrairBairrosDoTexto`
  - Open `tests/test_scraper.py` and add to class `TestExtrairBairrosDoTexto`:
    - `test_formato2_lista_plana_simples`: text with flat comma-separated list after header; assert all bairros returned
    - `test_formato2_e_como_separador`: last item is `"Vila Cemig e Vila Esperança"`; assert both names in result
    - `test_formato2_unico_bairro`: only one bairro in flat list; assert single-item list returned
    - `test_warning_emitido_quando_sem_bairros`: header present but no bairros; use `pytest caplog` to assert `WARNING` is logged
  - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x]* 3.1 Write property test for Property 1 — Formato 2 extracts all bairros
    - Use `hypothesis` `@given` with `st.lists(st.text(...), min_size=1)` to generate bairro name lists
    - Build Formato 2 text from generated list (join with `", "` and `" e "` before last item)
    - Call `extrair_bairros_do_texto` and assert all generated names appear in result (after `.title()`)
    - Add comment: `# Feature: bairros-afetados-extraction-fix, Property 1: Formato 2 extrai todos os bairros da lista plana`
    - _Requirements: 2.2, 2.3_

  - [x]* 3.2 Write property test for Property 2 — output invariant (no whitespace, no empties, no duplicates)
    - Use `@given` with both Formato 1 and Formato 2 text generators
    - Assert: every element has no leading/trailing whitespace, no empty string elements, no duplicate elements
    - Add comment: `# Feature: bairros-afetados-extraction-fix, Property 2: Invariante de saída`
    - _Requirements: 2.5, 3.5_

  - [x]* 3.3 Write property test for Property 3 — Formato 1 regression
    - Use `@given` with a Formato 1 text generator (city prefix lines)
    - Assert: all bairros from the generated lines appear in result, no city names appear as bairros
    - Add comment: `# Feature: bairros-afetados-extraction-fix, Property 3: Regressão Formato 1`
    - _Requirements: 3.1, 3.2, 3.4_

  - [x]* 3.4 Write property test for Property 4 — no header returns empty list
    - Use `@given(st.text())` filtered to exclude strings containing "BAIRROS AFETADOS"
    - Assert `extrair_bairros_do_texto(text) == []`
    - Add comment: `# Feature: bairros-afetados-extraction-fix, Property 4: Sem cabeçalho → lista vazia`
    - _Requirements: 3.3_

- [x] 4. Checkpoint — run full test suite
  - Run `pytest tests/ -v` and verify all existing tests plus new tests pass; ask the user if any test fails unexpectedly.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster minimal fix
- The only source file modified is `scraper.py`; no changes to callers (`processar_noticia`, `monitorar`, etc.)
- Hypothesis default settings (100 examples) are sufficient; no `@settings` override needed
- The `" e "` split is applied only to the **last fragment** of each line to avoid false splits on bairro names that contain the word "e"
