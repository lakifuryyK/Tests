import errno
import os
import socket

from . import create_app

app = create_app()


def _find_available_port(host: str, preferred: int) -> int:
    """Probe a short range of ports and return the first free slot."""

    for candidate in [preferred, *range(preferred + 1, preferred + 6)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, candidate))
            except OSError:
                continue
            return candidate
    return preferred


def _should_retry_with_fallback(error: OSError) -> bool:
    """Detect permission or access issues reported by the OS."""

    if error.errno in {errno.EACCES, errno.EADDRINUSE}:
        return True
    return getattr(error, "winerror", 0) in {10013, 10048}


if __name__ == "__main__":
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1")
    preferred_port = int(os.environ.get("FLASK_RUN_PORT", "5050"))
    debug = os.environ.get("FLASK_DEBUG", "0") in {"1", "true", "True"}

    try:
        app.run(host=host, port=preferred_port, debug=debug)
    except OSError as exc:
        if not _should_retry_with_fallback(exc):
            raise

        fallback_port = _find_available_port(host, preferred_port)
        if fallback_port == preferred_port:
            raise

        app.logger.warning(
            "Port %s is unavailable (%s). Switching to %s.",
            preferred_port,
            exc,
            fallback_port,
        )
        app.run(host=host, port=fallback_port, debug=debug)
