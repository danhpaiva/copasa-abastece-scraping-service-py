import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import Interrupcao

log = logging.getLogger(__name__)


def formatar_datetime(dt: Optional[datetime]) -> str:
    return dt.strftime("%d/%m/%Y às %H:%M") if dt else "não identificado"


def formatar_alerta_texto(interrupcao: Interrupcao) -> str:
    sep = "=" * 70
    status = "ATIVA AGORA" if interrupcao.esta_ativa() else "PROGRAMADA / ENCERRADA"
    linhas = [
        sep,
        f"  ALERTA DE INTERRUPÇÃO — {status}",
        sep,
        f"  Título   : {interrupcao.titulo}",
        f"  URL      : {interrupcao.url}",
        "",
        f"  Início   : {formatar_datetime(interrupcao.inicio)}",
        f"  Término  : {formatar_datetime(interrupcao.fim)}",
        "",
        f"  Cidades  : {', '.join(interrupcao.cidades) or 'não identificadas'}",
        f"  Bairros monitorados afetados: {', '.join(interrupcao.bairros_afetados)}",
        sep,
        "",
    ]
    return "\n".join(linhas)


def exibir_alerta_texto(interrupcao: Interrupcao) -> None:
    print(formatar_alerta_texto(interrupcao))


def formatar_resultado_texto(interrupcoes: list[Interrupcao]) -> str:
    if not interrupcoes:
        sep = "=" * 70
        return "\n".join([
            sep,
            "  Nenhuma interrupção encontrada para os bairros monitorados.",
            sep,
            "",
        ])

    linhas = [f"[RESULTADO] {len(interrupcoes)} alerta(s) único(s) encontrado(s):", ""]
    linhas.extend(formatar_alerta_texto(it) for it in interrupcoes)
    return "\n".join(linhas)


def exibir_resultado(interrupcoes: list[Interrupcao], modo_json: bool, output: Optional[Path] = None) -> None:
    payload = {
        "gerado_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "total_alertas": len(interrupcoes),
        "alertas": [i.to_dict() for i in interrupcoes],
    }

    if output:
        conteudo = json.dumps(payload, ensure_ascii=False, indent=2)
        output.write_text(conteudo, encoding="utf-8")
        output_txt = output.with_suffix(".txt")
        output_txt.write_text(formatar_resultado_texto(interrupcoes), encoding="utf-8")
        log.info(
            "Resultado salvo em %s e %s (%d alerta(s)).",
            output, output_txt, len(interrupcoes),
        )

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
