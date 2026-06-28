import re
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = (
    "https://www.copasa.com.br/wps/portal/internet/imprensa/noticias/"
    "informacoes-sobre-abastecimento"
)

BAIRROS_FILE = Path(__file__).parent.parent / "bairros.json"
CACHE_FILE   = Path(__file__).parent.parent / ".cache.json"

TIMEOUT_GLOBAL_S = 180

ARTICLE_SELECTORS = [
    "article",
    ".ibm-columns",
    ".ibm-col-1-1",
    "main",
    "#WCM_GLOBAL_CONTEXT",
    "body",
]

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
        "Gecko/20100101 Firefox/126.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4.1 Safari/605.1.15"
    ),
]

DATE_PATTERN = re.compile(
    r"do\s+dia\s+(\d{2}/\d{2}/\d{4})\s*\((\d{2}:\d{2}(?::\d{2})?)\)"
    r".*?"
    r"at[eé]\s+o\s+dia\s+(\d{2}/\d{2}/\d{4})\s*\((\d{2}:\d{2}(?::\d{2})?)\)",
    re.IGNORECASE | re.DOTALL,
)

TITLE_DATE_PATTERN = re.compile(r"^(\d{2}/\d{2})")

CITIES_PATTERN = re.compile(
    r"(?:cidade[s]?\s+de|munic[ií]pio[s]?\s+de)\s+([A-ZÁÉÍÓÚÀÂÊÔÃÕÇ\s,e]+?)(?=\.|$|\r|\n|o\s+abastecimento)",
    re.IGNORECASE,
)

BAIRROS_AFETADOS_HEADER = re.compile(r"BAIRROS\s+AFETADOS", re.IGNORECASE)

BAIRROS_CIDADE_PATTERN = re.compile(
    r"^([A-ZÁÉÍÓÚÀÂÊÔÃÕÇ][^:]{2,40}):\s*(.+)$",
    re.MULTILINE,
)
