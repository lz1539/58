import unittest

import main


class ClickDiagnosticsTest(unittest.TestCase):
    def test_format_candidate_log_label(self):
        candidate = {"name": "张三", "age": 30, "infoid": "123"}

        self.assertEqual(
            main.format_candidate_log_label(candidate, "infoid:123"),
            "张三(30) | key=infoid:123",
        )

    def test_format_candidate_log_label_uses_unknown_values(self):
        candidate = {}

        self.assertEqual(
            main.format_candidate_log_label(candidate, "name:|age:"),
            "未知候选人(年龄未知) | key=name:|age:",
        )


if __name__ == "__main__":
    unittest.main()
