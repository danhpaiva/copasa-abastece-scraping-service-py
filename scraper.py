"""
Copasa Abastecimento Scraper
Monitora interrupções no abastecimento de água em bairros configurados.

Uso:
  python scraper.py                  # varredura completa, saída texto
  python scraper.py --json           # varredura completa, saída JSON em stdout
  python scraper.py <url>            # processa URL direta
  python scraper.py <url> --json     # URL direta com saída JSON

Exit codes:
  0 — nenhuma interrupção encontrada nos bairros monitorados
  1 — uma ou mais interrupções encontradas
  2 — erro de execução (rede, parsing, etc.)
"""

from scraper.cli import main

if __name__ == "__main__":
    main()
