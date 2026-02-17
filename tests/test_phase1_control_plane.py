import unittest

from voice_hotkey.runtime.controller import (
    DEFAULT_MAX_CONNECTION_WORKERS,
    MAX_CONNECTION_WORKERS_LIMIT,
    resolve_max_connection_workers,
)


class Phase1ControlPlaneTests(unittest.TestCase):
    def test_resolve_max_connection_workers_defaults(self) -> None:
        self.assertEqual(resolve_max_connection_workers(None), DEFAULT_MAX_CONNECTION_WORKERS)

    def test_resolve_max_connection_workers_clamps_low_values(self) -> None:
        self.assertEqual(resolve_max_connection_workers(0), 1)
        self.assertEqual(resolve_max_connection_workers(-5), 1)

    def test_resolve_max_connection_workers_clamps_high_values(self) -> None:
        self.assertEqual(resolve_max_connection_workers(MAX_CONNECTION_WORKERS_LIMIT + 1), MAX_CONNECTION_WORKERS_LIMIT)

    def test_resolve_max_connection_workers_accepts_valid_values(self) -> None:
        self.assertEqual(resolve_max_connection_workers(2), 2)
        self.assertEqual(resolve_max_connection_workers(MAX_CONNECTION_WORKERS_LIMIT), MAX_CONNECTION_WORKERS_LIMIT)


if __name__ == "__main__":
    unittest.main()
