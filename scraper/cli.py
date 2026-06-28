import argparse
import logging
import sys
from pathlib import Path

from .config import CACHE_FILE, TIMEOUT_GLOBAL_S
from .monitor import monitorar, monitorar_url_direta

log = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitora interrupções de abastecimento da Copasa.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  nenhuma interrupção encontrada\n"
            "  1  uma ou mais interrupções encontradas\n"
            "  2  erro de execução\n"
        ),
    )
    parser.add_argument("url", nargs="?", help="URL direta de uma notícia (opcional)")
    parser.add_argument("--json", dest="json_output", action="store_true",
                        help="Emite resultado em JSON para stdout (logs vão para stderr)")
    parser.add_argument("--janela", type=int, default=14, metavar="DIAS",
                        help="Janela de dias para filtrar notícias pelo título (padrão: 14)")
    parser.add_argument("--no-cache", dest="no_cache", action="store_true",
                        help="Ignora o cache e reprocessa todos os artigos")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_GLOBAL_S, metavar="SEG",
                        help=f"Timeout global da sessão em segundos (padrão: {TIMEOUT_GLOBAL_S})")
    parser.add_argument("--output", type=Path, default=None, metavar="ARQUIVO",
                        help="Grava o resultado em JSON no arquivo informado (ex: alerts.json)")
    parser.add_argument("--debug", action="store_true",
                        help="Habilita logs de nível DEBUG")
    return parser


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(levelname)-8s %(message)s",
    )

    args = _build_parser().parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.no_cache and CACHE_FILE.exists():
        CACHE_FILE.unlink()
        log.info("Cache limpo.")

    if args.url:
        code = monitorar_url_direta(args.url, modo_json=args.json_output, output=args.output)
    else:
        code = monitorar(modo_json=args.json_output, janela_dias=args.janela, timeout_s=args.timeout, output=args.output)

    sys.exit(code)
