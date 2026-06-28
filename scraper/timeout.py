import signal


class TimeoutError(Exception):
    pass


def _handler_timeout(signum, frame):
    raise TimeoutError("Timeout global atingido.")


class timeout_global:
    """Context manager para timeout global. Usa SIGALRM no Unix; no Windows é no-op."""

    def __init__(self, segundos: int):
        self.segundos = segundos
        self._suportado = hasattr(signal, "SIGALRM")

    def __enter__(self):
        if self._suportado:
            signal.signal(signal.SIGALRM, _handler_timeout)
            signal.alarm(self.segundos)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._suportado:
            signal.alarm(0)
        return False
