import unittest
from pathlib import Path

import main


class ManagedEdgeShutdownTest(unittest.TestCase):
    def test_parse_managed_edge_process_ids_matches_all_processes_under_user_data_dir(self):
        user_data_dir = Path("C:/Users/li153/Desktop/新建文件夹/58/edge_profile")
        command_lines = [
            '17508\t"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" --remote-debugging-port=39222 --user-data-dir=C:\\Users\\li153\\Desktop\\新建文件夹\\58\\edge_profile --profile-directory=Default about:blank',
            '32904\t"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" --type=crashpad-handler --user-data-dir=C:\\Users\\li153\\Desktop\\新建文件夹\\58\\edge_profile /prefetch:4',
            '9988\t"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" --remote-debugging-port=39223 --user-data-dir=C:\\other\\edge_profile --profile-directory=Default',
        ]

        self.assertEqual(
            main.parse_managed_edge_process_ids(command_lines, user_data_dir),
            ["17508", "32904"],
        )


if __name__ == "__main__":
    unittest.main()
