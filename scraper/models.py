from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .utils import BRT


@dataclass
class Interrupcao:
    titulo: str
    url: str
    cidades: list[str]
    inicio: Optional[datetime]
    fim: Optional[datetime]
    bairros_afetados: list[str]
    texto_bruto: str = field(repr=False)

    def esta_ativa(self) -> bool:
        agora = datetime.now(BRT)
        if self.inicio and self.fim:
            return self.inicio <= agora <= self.fim
        return False

    def esta_encerrada(self) -> bool:
        if self.fim:
            return datetime.now(BRT) > self.fim
        return False

    def to_dict(self) -> dict:
        return {
            "titulo": self.titulo,
            "url": self.url,
            "cidades": self.cidades,
            "inicio": self.inicio.isoformat() if self.inicio else None,
            "fim": self.fim.isoformat() if self.fim else None,
            "esta_ativa": self.esta_ativa(),
            "bairros_afetados": self.bairros_afetados,
        }
