import unittest
import builtins
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

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


class InteractiveInput(StringIO):
    def isatty(self) -> bool:
        return True


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
                False,
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

    def test_reaching_deadline_closes_edge_for_current_user_data_dir_when_enabled(self):
        fake_time = FakeTime()
        closed_dirs: list[Path] = []

        original_time = main.time.time
        original_sleep = main.time.sleep
        original_strftime = main.time.strftime
        original_run_once = main.run_once
        original_close_managed_edge = getattr(main, "close_managed_edge_processes", None)
        try:
            main.time.time = fake_time.time
            main.time.sleep = fake_time.sleep
            main.time.strftime = fake_time.strftime

            def fake_run_once(_context, page, _cycle, login_timeout_seconds=None):
                fake_time.current += 2
                return page

            def fake_close_managed_edge_processes(user_data_dir):
                closed_dirs.append(user_data_dir)

            main.run_once = fake_run_once
            main.close_managed_edge_processes = fake_close_managed_edge_processes
            user_data_dir = Path("C:/Users/li153/Desktop/新建文件夹/58/edge_profile")
            main.run_periodically(
                object(),
                object(),
                user_data_dir,
                FakeBrowser(),
                FakeContext(),
                FakePage(),
                1,
                True,
            )
        finally:
            main.time.time = original_time
            main.time.sleep = original_sleep
            main.time.strftime = original_strftime
            main.run_once = original_run_once
            if original_close_managed_edge is None:
                delattr(main, "close_managed_edge_processes")
            else:
                main.close_managed_edge_processes = original_close_managed_edge

        self.assertEqual(closed_dirs, [user_data_dir])

    def test_one_minute_run_does_not_wait_ten_minutes_before_exit(self):
        fake_time = FakeTime()
        closed_dirs: list[Path] = []

        original_time = main.time.time
        original_sleep = main.time.sleep
        original_strftime = main.time.strftime
        original_run_once = main.run_once
        original_close_managed_edge = getattr(main, "close_managed_edge_processes", None)
        try:
            main.time.time = fake_time.time
            main.time.sleep = fake_time.sleep
            main.time.strftime = fake_time.strftime

            def fake_run_once(_context, page, _cycle, login_timeout_seconds=None):
                fake_time.current += 2
                return page

            def fake_close_managed_edge_processes(user_data_dir):
                closed_dirs.append(user_data_dir)

            main.run_once = fake_run_once
            main.close_managed_edge_processes = fake_close_managed_edge_processes
            user_data_dir = Path("C:/Users/li153/Desktop/新建文件夹/58/edge_profile")
            main.run_periodically(
                object(),
                object(),
                user_data_dir,
                FakeBrowser(),
                FakeContext(),
                FakePage(),
                60,
                True,
            )
        finally:
            main.time.time = original_time
            main.time.sleep = original_sleep
            main.time.strftime = original_strftime
            main.run_once = original_run_once
            if original_close_managed_edge is None:
                delattr(main, "close_managed_edge_processes")
            else:
                main.close_managed_edge_processes = original_close_managed_edge

        self.assertEqual(fake_time.sleeps, [58])
        self.assertEqual(closed_dirs, [user_data_dir])


class PromptRunDurationTest(unittest.TestCase):
    def test_option_9_toggles_auto_close_and_persists_choice(self):
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            original_stdin = main.sys.stdin
            original_input = builtins.input
            original_stdout = main.sys.stdout
            original_get_base_dir = main.get_base_dir
            try:
                main.get_base_dir = lambda: base_dir
                main.sys.stdin = InteractiveInput("9\n1\n")
                captured = StringIO()
                main.sys.stdout = captured

                run_duration_seconds, auto_close_edge_on_exit = main.prompt_run_duration_seconds()
            finally:
                main.sys.stdin = original_stdin
                main.sys.stdout = original_stdout
                main.get_base_dir = original_get_base_dir

            self.assertEqual(run_duration_seconds, 3600)
            self.assertTrue(auto_close_edge_on_exit)
            self.assertIn("9. 到时关闭浏览器开关（当前：不关浏览器）", captured.getvalue())
            original_get_base_dir = main.get_base_dir
            try:
                main.get_base_dir = lambda: base_dir
                self.assertTrue(main.load_app_config().get("auto_close_edge_on_exit"))
            finally:
                main.get_base_dir = original_get_base_dir

    def test_option_10_runs_for_one_minute_without_changing_switch(self):
        with TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            original_stdin = main.sys.stdin
            original_stdout = main.sys.stdout
            original_get_base_dir = main.get_base_dir
            try:
                main.get_base_dir = lambda: base_dir
                main.save_app_config({"auto_close_edge_on_exit": False})
                main.sys.stdin = InteractiveInput("10\n")
                captured = StringIO()
                main.sys.stdout = captured

                run_duration_seconds, auto_close_edge_on_exit = main.prompt_run_duration_seconds()
            finally:
                main.sys.stdin = original_stdin
                main.sys.stdout = original_stdout
                main.get_base_dir = original_get_base_dir

            self.assertEqual(run_duration_seconds, 60)
            self.assertFalse(auto_close_edge_on_exit)
            self.assertIn("10. 测试运行 1 分钟", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
