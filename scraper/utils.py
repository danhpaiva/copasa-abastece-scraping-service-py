import json
import unicodedata
from datetime import datetime
from typing import Optional

from .config import BAIRROS_FILE, TITLE_DATE_PATTERN


def normalizar(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sem_acento.lower().strip()


def carregar_bairros() -> tuple[list[str], dict[str, str], list[str]]:
    with open(BAIRROS_FILE, encoding="utf-8") as f:
        dados = json.load(f)
    return dados["bairros"], dados.get("aliases", {}), dados.get("cidades_alvo", [])


def parse_datetime(data_str: str, hora_str: str) -> Optional[datetime]:
    if hora_str.count(":") == 1:
        hora_str += ":00"
    try:
        return datetime.strptime(f"{data_str} {hora_str}", "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return None


def dentro_da_janela(titulo: str, janela_dias: int = 14) -> bool:
    match = TITLE_DATE_PATTERN.match(titulo.strip())
    if not match:
        return True

    hoje = datetime.now().date()
    try:
        data = datetime.strptime(f"{match.group(1)}/{hoje.year}", "%d/%m/%Y").date()
        if (data - hoje).days > 30:
            data = data.replace(year=hoje.year - 1)
    except ValueError:
        return True

    return (hoje - data).days <= janela_dias
