from __future__ import annotations

import time
import unittest

from gdictate_core.preflight import preflight_report
from gdictate_core.settings import default_settings, settings_schema, settings_snapshot


class PerformanceSmokeTests(unittest.TestCase):
    def test_settings_hot_path_is_fast(self) -> None:
        start = time.perf_counter()
        for _ in range(250):
            default_settings()
            settings_schema()
        elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 0.75)

    def test_settings_snapshot_is_fast(self) -> None:
        start = time.perf_counter()
        for _ in range(50):
            settings_snapshot()
        elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 0.75)

    def test_preflight_stays_interactive(self) -> None:
        start = time.perf_counter()
        report = preflight_report()
        elapsed = time.perf_counter() - start

        self.assertTrue(report.checks)
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
