from pathlib import Path
import unittest

import main


class CdpPortDetectionTest(unittest.TestCase):
    def test_parse_running_edge_cdp_port_supports_chinese_user_data_dir(self):
        command_lines = [
            (
                '"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" '
                "--remote-debugging-port=65487 "
                "--user-data-dir=C:\\Users\\li153\\Desktop\\新建文件夹\\58\\edge_profile "
                "--profile-directory=Default"
            )
        ]

        self.assertEqual(
            main.parse_running_edge_cdp_port(
                command_lines,
                Path("C:\\Users\\li153\\Desktop\\新建文件夹\\58\\edge_profile"),
                lambda port: port == 65487,
            ),
            65487,
        )

    def test_parse_running_edge_cdp_port_supports_quoted_user_data_dir(self):
        command_lines = [
            (
                '"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" '
                "--remote-debugging-port=65487 "
                '--user-data-dir="C:\\Users\\li153\\Desktop\\新建文件夹\\58\\edge_profile" '
                "--profile-directory=Default"
            )
        ]

        self.assertEqual(
            main.parse_running_edge_cdp_port(
                command_lines,
                Path("C:\\Users\\li153\\Desktop\\新建文件夹\\58\\edge_profile"),
                lambda port: port == 65487,
            ),
            65487,
        )


if __name__ == "__main__":
    unittest.main()
