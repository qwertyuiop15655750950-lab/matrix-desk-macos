"""实际 REDnote /chat 跳转 404 后，禁止误报已登录/没有新消息。"""
import unittest
from unittest.mock import Mock
from app import messages


class MessagePageTests(unittest.TestCase):
    def test_home_and_404_are_errors_before_dom_read(self):
        for url in ("https://www.rednote.com/404?source=/chat",
                    "https://www.rednote.com/?source=404",
                    "https://www.xiaohongshu.com/explore",
                    "https://www.rednote.com/chat-not-found",
                    "http://www.xiaohongshu.com/chat",
                    "https://www.rednote.com.evil.test/chat",
                    "https://www.rednote.com:444/chat",
                    "https://user@www.rednote.com/chat",
                    "https://[invalid/"):
            for method in (messages.is_logged_in, messages.conversation_list, messages.read_thread):
                with self.subTest(url=url, method=method.__name__):
                    page=Mock(url=url)
                    with self.assertRaisesRegex(RuntimeError, "不能据此判断没有新消息"):
                        method(page, messages.DEFAULT_SELECTORS)
                    page.evaluate.assert_not_called()

    def test_empty_unrecognized_dom_is_not_reported_as_no_messages(self):
        page=Mock(url='https://www.rednote.com/chat')
        page.evaluate.side_effect=[[],False]
        with self.assertRaisesRegex(RuntimeError,'不能判断没有消息'):
            messages.conversation_list(page,messages.DEFAULT_SELECTORS)

    def test_dom_read_failure_is_not_reported_as_no_messages(self):
        page=Mock(url='https://www.rednote.com/chat')
        page.evaluate.side_effect=RuntimeError('DOM gone')
        with self.assertRaisesRegex(RuntimeError,'会话读取失败'):
            messages.conversation_list(page,messages.DEFAULT_SELECTORS)

    def test_actual_domestic_chat_can_check_login(self):
        page=Mock(url="https://www.xiaohongshu.com/chat")
        page.evaluate.return_value=True
        self.assertTrue(messages.is_logged_in(page,messages.DEFAULT_SELECTORS))

    def test_domestic_conversation_path_is_valid(self):
        page=Mock(url="https://www.xiaohongshu.com/chat/123?source=message")
        page.evaluate.return_value=[]
        self.assertEqual(messages.read_thread(page,messages.DEFAULT_SELECTORS),[])


if __name__ == "__main__":
    unittest.main()
