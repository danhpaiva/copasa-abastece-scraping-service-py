import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import Interrupcao

log = logging.getLogger(__name__)


def formatar_datetime(dt: Optional[datetime]) -> str:
    return dt.strftime("%d/%m/%Y às %H:%M") if dt else "não identificado"


def exibir_alerta_texto(interrupcao: Interrupcao) -> None:
    sep = "=" * 70
    status = "ATIVA AGORA" if interrupcao.esta_ativa() else "PROGRAMADA / ENCERRADA"
    print(sep)
    print(f"  ALERTA DE INTERRUPÇÃO — {status}")
    print(sep)
    print(f"  Título   : {interrupcao.titulo}")
    print(f"  URL      : {interrupcao.url}")
    print()
    print(f"  Início   : {formatar_datetime(interrupcao.inicio)}")
    print(f"  Término  : {formatar_datetime(interrupcao.fim)}")
    print()
    print(f"  Cidades  : {', '.join(interrupcao.cidades) or 'não identificadas'}")
    print(f"  Bairros monitorados afetados: {', '.join(interrupcao.bairros_afetados)}")
    print(sep)
    print()


def exibir_resultado(interrupcoes: list[Interrupcao], modo_json: bool, output: Optional[Path] = None) -> None:
    payload = {
        "gerado_em": datetime.now().isoformat(),
        "total_alertas": len(interrupcoes),
        "alertas": [i.to_dict() for i in interrupcoes],
    }

    if output:
        conteudo = json.dumps(payload, ensure_ascii=False, indent=2)
        output.write_text(conteudo, encoding="utf-8")
        log.info("Resultado salvo em %s (%d alerta(s)).", output, len(interrupcoes))

    if modo_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not interrupcoes:
        print("=" * 70)
        print("  Nenhuma interrupção encontrada para os bairros monitorados.")
        print("=" * 70)
        return

    print(f"[RESULTADO] {len(interrupcoes)} alerta(s) único(s) encontrado(s):\n")
    for it in interrupcoes:
        exibir_alerta_texto(it)
