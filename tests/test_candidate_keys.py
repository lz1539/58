import unittest

import main


class CandidateKeyTest(unittest.TestCase):
    def test_build_candidate_key_prefers_resumeid(self):
        candidate = {
            "infoid": "重复-info",
            "resumeid": "resume-123",
            "name": "张三",
            "age": 30,
        }

        self.assertEqual(main.build_candidate_key(candidate), "resumeid:resume-123")

    def test_build_candidate_key_ignores_infoid_without_resumeid(self):
        candidate = {
            "infoid": "重复-info",
            "name": "张三",
            "age": 30,
        }

        self.assertEqual(main.build_candidate_key(candidate), "name:张三|age:30")

    def test_normalize_candidate_from_api_keeps_infoid_and_resumeid_separate(self):
        candidate = main.normalize_candidate_from_api(
            {
                "name": "张三",
                "sex": "男",
                "age": "30",
                "infoId": "info-1",
                "resumeId": "resume-1",
            },
            {},
        )

        self.assertEqual(candidate["infoid"], "info-1")
        self.assertEqual(candidate["resumeid"], "resume-1")


if __name__ == "__main__":
    unittest.main()
