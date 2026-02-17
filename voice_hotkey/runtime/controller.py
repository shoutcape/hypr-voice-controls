from __future__ import annotations

import socket
from concurrent.futures import ThreadPoolExecutor
from logging import Logger
from typing import Callable


DEFAULT_MAX_CONNECTION_WORKERS = 4
MAX_CONNECTION_WORKERS_LIMIT = 32

ConnectionHandler = Callable[[socket.socket], None]


def resolve_max_connection_workers(requested: int | None) -> int:
    if requested is None:
        return DEFAULT_MAX_CONNECTION_WORKERS
    if requested < 1:
        return 1
    if requested > MAX_CONNECTION_WORKERS_LIMIT:
        return MAX_CONNECTION_WORKERS_LIMIT
    return requested


def _safe_handle_connection(connection_handler: ConnectionHandler, conn: socket.socket, logger: Logger) -> None:
    try:
        connection_handler(conn)
    except Exception as exc:
        logger.exception("Voice daemon connection handler crashed: %s", exc)
        try:
            conn.close()
        except OSError:
            pass


def serve_with_thread_pool(
    server: socket.socket,
    connection_handler: ConnectionHandler,
    *,
    logger: Logger,
    max_workers: int | None = None,
) -> None:
    worker_count = resolve_max_connection_workers(max_workers)
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="voice-daemon") as executor:
        while True:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                logger.warning("Voice daemon accept loop error: %s", exc)
                continue

            executor.submit(_safe_handle_connection, connection_handler, conn, logger)
