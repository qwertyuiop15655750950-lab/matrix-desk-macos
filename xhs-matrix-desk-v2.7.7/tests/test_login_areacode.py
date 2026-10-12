"""区号点击不能把平台不支持或无变化误报为成功。"""
import unittest
from unittest.mock import Mock

from app.profiles import open_areacode_menu, force_click_areacode


class CountryCodeTests(unittest.TestCase):
    def page(self, *, opened=False, after_click=False, text="", controls=True,
             url="https://www.rednote.com/"):
        page = Mock(url=url)
        menu, button, body = Mock(), Mock(), Mock()
        menu.is_visible.side_effect = [opened, after_click]
        if not after_click:
            menu.wait_for.side_effect = TimeoutError("no dropdown")
        button.is_visible.return_value = True
        body.inner_text.return_value = text
        page.locator.side_effect = lambda selector: {
            'input[placeholder="搜索国家/地区"]:visible': menu,
            '.country-code-select, .country-code': Mock(all=lambda: [button] if controls else []),
            'body': body,
        }[selector]
        return page, button

    def test_rednote_country_menu_opens_without_hardcoded_code(self):
        page, button = self.page(after_click=True)
        self.assertEqual(open_areacode_menu(page)["status"], "opened")
        button.click.assert_called_once_with(timeout=3000)
        page.evaluate.assert_not_called()

    def test_already_open_menu_is_not_toggled_closed(self):
        page, button = self.page(opened=True)
        self.assertTrue(open_areacode_menu(page)["ok"])
        button.click.assert_not_called()

    def test_domestic_fixed_code_has_truthful_error(self):
        page, _ = self.page(text="暂不支持切换地区，敬请期待",
                            url="https://www.xiaohongshu.com/explore")
        result = open_areacode_menu(page)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unsupported")
        self.assertIn("https://www.rednote.com/", result["error"])

    def test_click_without_dropdown_is_failure(self):
        page, _ = self.page()
        self.assertFalse(force_click_areacode(page))

    def test_missing_control_does_not_report_success(self):
        page, button = self.page(controls=False)
        self.assertEqual(open_areacode_menu(page)["status"], "not_found")
        button.click.assert_not_called()

    def test_untrusted_or_malformed_pages_are_never_clicked(self):
        for url in ("https://rednote.com.evil.test/", "http://www.rednote.com/",
                    "https://www.rednote.com:444/", "https://user@www.rednote.com/",
                    "https://[invalid/"):
            with self.subTest(url=url):
                page, button = self.page(url=url)
                self.assertEqual(open_areacode_menu(page)["status"], "unsupported_page")
                page.locator.assert_not_called()
                button.click.assert_not_called()

    def test_click_error_is_not_success(self):
        page, button = self.page()
        button.click.side_effect = TimeoutError("covered")
        self.assertEqual(open_areacode_menu(page)["status"], "click_failed")


if __name__ == "__main__":
    unittest.main()
