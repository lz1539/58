import unittest

import main


class FakePage:
    def __init__(self, url: str):
        self.url = url
        self.calls: list[str] = []

    def reload(self, wait_until: str):
        self.calls.append(f"reload:{wait_until}")

    def goto(self, url: str, wait_until: str):
        self.calls.append(f"goto:{url}:{wait_until}")
        self.url = url

    def wait_for_load_state(self, state: str):
        self.calls.append(f"wait_for_load_state:{state}")

    def bring_to_front(self):
        self.calls.append("bring_to_front")


class TargetPageFlowTest(unittest.TestCase):
    def test_target_page_is_reloaded_before_business_flow(self):
        page = FakePage(main.TARGET_URL)
        business_calls: list[str] = []

        original_wait_for_login = main.wait_for_login
        original_wait_for_candidate_list = main.wait_for_candidate_list
        original_click_matching_online_chat = main.click_matching_online_chat
        try:
            main.wait_for_login = lambda context, page, timeout_seconds=None: page
            main.wait_for_candidate_list = lambda page: business_calls.append("wait_for_candidate_list")
            main.click_matching_online_chat = lambda page, cycle: business_calls.append(f"click:{cycle}")

            returned_page = main.run_once(object(), page, 3)
        finally:
            main.wait_for_login = original_wait_for_login
            main.wait_for_candidate_list = original_wait_for_candidate_list
            main.click_matching_online_chat = original_click_matching_online_chat

        self.assertIs(returned_page, page)
        self.assertEqual(page.calls, ["reload:domcontentloaded", "wait_for_load_state:domcontentloaded", "bring_to_front"])
        self.assertEqual(business_calls, ["wait_for_candidate_list", "click:3"])

    def test_non_target_page_enters_target_page_before_business_flow(self):
        page = FakePage("about:blank")
        business_calls: list[str] = []

        original_wait_for_login = main.wait_for_login
        original_wait_for_candidate_list = main.wait_for_candidate_list
        original_click_matching_online_chat = main.click_matching_online_chat
        try:
            main.wait_for_login = lambda context, page, timeout_seconds=None: page
            main.wait_for_candidate_list = lambda page: business_calls.append("wait_for_candidate_list")
            main.click_matching_online_chat = lambda page, cycle: business_calls.append(f"click:{cycle}")

            returned_page = main.run_once(object(), page, 4)
        finally:
            main.wait_for_login = original_wait_for_login
            main.wait_for_candidate_list = original_wait_for_candidate_list
            main.click_matching_online_chat = original_click_matching_online_chat

        self.assertIs(returned_page, page)
        self.assertEqual(
            page.calls,
            [f"goto:{main.TARGET_URL}:domcontentloaded", "wait_for_load_state:domcontentloaded", "bring_to_front"],
        )
        self.assertEqual(business_calls, ["wait_for_candidate_list", "click:4"])


if __name__ == "__main__":
    unittest.main()
