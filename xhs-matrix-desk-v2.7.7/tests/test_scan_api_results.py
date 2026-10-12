"""超时停止后的部分结果应归档，并保留可供 UI 显示的完成原因。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.webapp import Api


class PartialScanApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.api = Api(str(Path(self.tmp.name) / "test.db"))
        self.partial = {
            "ok": True, "stopped": True, "timed_out": True, "read": 4,
            "items": [{"nickname": "访客", "user_id": "real-profile-id",
                       "text": "多少钱", "keyword": "多少钱",
                       "contacts": {"wechat": "", "phone": "", "qq": ""}}],
        }

    def tearDown(self):
        self.api.close_thread_conn()
        self.tmp.cleanup()

    def test_dy_timeout_preserves_partial_comments_and_reason(self):
        aid = self.api.hub_save("douyin", {"nickname": "测试"})["id"]
        with patch.object(self.api._dy_browsers, "scan", return_value=self.partial):
            result = self.api.dy_scan_video(aid, "https://www.douyin.com/video/local-test")
        self.assertTrue(result["ok"])
        self.assertTrue(result["timed_out"])
        self.assertTrue(result["stopped"])
        self.assertEqual((result["fetched"], result["read"]), (1, 4))
        rows = self.api.dy_comments(aid)["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], "real-profile-id")

    def test_xhs_timeout_preserves_partial_archive_and_reason(self):
        aid = self.api.hub_save("xiaohongshu", {"nickname": "测试"})["id"]
        browser = SimpleNamespace(ctx=SimpleNamespace(pages=[object()]))
        with patch.object(self.api, "_ensure_actor") as actor, patch(
            "app.browser_plugin_bridge.scan_with_plugin", return_value=self.partial
        ):
            actor.return_value.call.side_effect = lambda fn, **kw: fn(browser)
            result = self.api.hub_collect("xiaohongshu", aid, "https://www.xiaohongshu.com/explore/local-test")
        self.assertTrue(result["ok"])
        self.assertTrue(result["timed_out"])
        self.assertEqual((result["added"], result["read"]), (1, 4))
        self.assertEqual(len(self.api.hub_collected()["items"]), 1)


if __name__ == "__main__":
    unittest.main()
