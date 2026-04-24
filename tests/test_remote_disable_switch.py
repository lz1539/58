import unittest

import main


class RemoteDisableSwitchTest(unittest.TestCase):
    def test_non_frozen_run_skips_remote_disable_check(self):
        original_frozen = getattr(main.sys, "frozen", None)
        original_probe = getattr(main, "probe_remote_disable_switch", None)
        try:
            if hasattr(main.sys, "frozen"):
                delattr(main.sys, "frozen")

            def fail_probe():
                raise AssertionError("不应在脚本模式下探测远程禁用开关")

            main.probe_remote_disable_switch = fail_probe
            self.assertFalse(main.is_remote_disable_enabled())
        finally:
            if original_probe is None:
                delattr(main, "probe_remote_disable_switch")
            else:
                main.probe_remote_disable_switch = original_probe

            if original_frozen is not None:
                main.sys.frozen = original_frozen

    def test_frozen_run_returns_true_only_when_disable_response_matches(self):
        original_frozen = getattr(main.sys, "frozen", None)
        original_probe = getattr(main, "probe_remote_disable_switch", None)
        try:
            main.sys.frozen = True
            main.probe_remote_disable_switch = lambda: True
            self.assertTrue(main.is_remote_disable_enabled())
        finally:
            if original_probe is None:
                delattr(main, "probe_remote_disable_switch")
            else:
                main.probe_remote_disable_switch = original_probe

            if original_frozen is None:
                delattr(main.sys, "frozen")
            else:
                main.sys.frozen = original_frozen

    def test_probe_failures_are_treated_as_allowed(self):
        original_frozen = getattr(main.sys, "frozen", None)
        original_probe = getattr(main, "probe_remote_disable_switch", None)
        try:
            main.sys.frozen = True

            def raise_timeout():
                raise TimeoutError("timeout")

            main.probe_remote_disable_switch = raise_timeout
            self.assertFalse(main.is_remote_disable_enabled())
        finally:
            if original_probe is None:
                delattr(main, "probe_remote_disable_switch")
            else:
                main.probe_remote_disable_switch = original_probe

            if original_frozen is None:
                delattr(main.sys, "frozen")
            else:
                main.sys.frozen = original_frozen


if __name__ == "__main__":
    unittest.main()
