import unittest

import main


class FakeTime:
    def __init__(self):
        self.current = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds

    def strftime(self, _format: str) -> str:
        return "2026-04-18 12:00:00"


class FakeBrowser:
    def is_connected(self) -> bool:
        return True


class FakePage:
    def is_closed(self) -> bool:
        return False


class FakeContext:
    def new_page(self):
        return FakePage()


class PeriodicScheduleTest(unittest.TestCase):
    def test_short_final_remainder_is_rounded_and_allows_next_cycle(self):
        fake_time = FakeTime()
        cycles: list[int] = []

        original_time = main.time.time
        original_sleep = main.time.sleep
        original_strftime = main.time.strftime
        original_run_once = main.run_once
        try:
            main.time.time = fake_time.time
            main.time.sleep = fake_time.sleep
            main.time.strftime = fake_time.strftime

            def fake_run_once(_context, page, cycle, login_timeout_seconds=None):
                cycles.append(cycle)
                fake_time.current += 2
                return page

            main.run_once = fake_run_once
            main.run_periodically(
                object(),
                object(),
                object(),
                FakeBrowser(),
                FakeContext(),
                FakePage(),
                601,
            )
        finally:
            main.time.time = original_time
            main.time.sleep = original_sleep
            main.time.strftime = original_strftime
            main.run_once = original_run_once

        self.assertEqual(cycles, [1, 2])
        self.assertEqual(fake_time.sleeps, [main.REFRESH_INTERVAL_SECONDS])

    def test_round_up_to_refresh_interval(self):
        cases = [
            (0, 0),
            (-1, 0),
            (1, 600),
            (599, 600),
            (600, 600),
            (601, 1200),
        ]
        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(main.round_up_to_refresh_interval(seconds), expected)


if __name__ == "__main__":
    unittest.main()
