"""Real extension on synthetic pages; all network requests are fulfilled locally."""
import tempfile
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from app.browser_plugin_bridge import extension_launch_options

HTML = '''<!doctype html><html><body><div id="feed" style="height:200px;overflow:auto">
<div style="height:3000px">测试内容</div></div><button id="engage">点赞</button>
<script>window.engagements=0;document.getElementById('engage').onclick=()=>window.engagements++;</script>
</body></html>'''


class BrowseAssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.pw = sync_playwright().start()
        cls.ctx = cls.pw.chromium.launch_persistent_context(str(Path(cls.tmp.name)/'profile'),
            headless=True, viewport={'width':900,'height':600}, service_workers='block',
            **extension_launch_options())
        cls.ctx.route('**/*', lambda route: route.fulfill(status=200,content_type='text/html',body=HTML))

    @classmethod
    def tearDownClass(cls):
        cls.ctx.close(); cls.pw.stop(); cls.tmp.cleanup()

    def page(self, creator=False):
        page = self.ctx.new_page(); self.addCleanup(page.close)
        page.goto('https://creator.rednote.com/publish' if creator else 'https://www.rednote.com/explore/fixture')
        page.wait_for_selector('#matrix-browse-assistant[data-ready="true"]')
        page.locator('#matrix-browse-assistant').locator('#toggle').click()
        return page

    def start(self, page):
        panel = page.locator('#matrix-browse-assistant')
        panel.locator('#select').click(); page.locator('#feed').click(position={'x':50,'y':50})
        panel.locator('#dwell').fill('5'); panel.locator('#start').click()
        self.assertEqual(page.locator('#matrix-browse-assistant').get_attribute('data-state'),'running')

    def test_bounded_scroll_never_engages_and_stop_has_no_further_actions(self):
        page = self.page(); self.start(page)
        page.wait_for_function("document.querySelector('#feed').scrollTop > 0", timeout=8000)
        self.assertEqual(page.evaluate('window.engagements'),0)
        page.locator('#matrix-browse-assistant').locator('#stop').click()
        position = page.locator('#feed').evaluate('(e)=>e.scrollTop')
        page.wait_for_timeout(700)
        self.assertEqual(page.locator('#feed').evaluate('(e)=>e.scrollTop'),position)

    def test_region_warning_stops_and_cannot_restart(self):
        page = self.page(); self.start(page)
        page.evaluate("document.body.insertAdjacentHTML('beforeend','<div role=\"alert\">區域性顯示調整：部分功能目前在特定地區無法使用</div>')")
        page.wait_for_function("document.querySelector('#matrix-browse-assistant').dataset.state === 'idle'")
        self.assertEqual(page.locator('#feed').evaluate('(e)=>e.scrollTop'),0)
        page.locator('#matrix-browse-assistant').locator('#start').click()
        self.assertEqual(page.locator('#matrix-browse-assistant').get_attribute('data-last-event'),'start_blocked')

    def test_route_change_stops_and_new_page_stays_idle(self):
        page = self.page(); self.start(page)
        page.evaluate("history.pushState({},'', '/explore/another')")
        page.wait_for_function("document.querySelector('#matrix-browse-assistant').dataset.state === 'idle'")
        self.assertIn('地址已改变',page.locator('#matrix-browse-assistant').locator('#status').inner_text())
        page.reload(); page.wait_for_selector('#matrix-browse-assistant[data-state="idle"]')

    def test_background_pauses_and_does_not_resume_automatically(self):
        page = self.page(); self.start(page)
        page.evaluate("window.dispatchEvent(new Event('blur'))")
        self.assertEqual(page.locator('#matrix-browse-assistant').get_attribute('data-state'),'paused')
        page.evaluate("window.dispatchEvent(new Event('focus'))"); page.wait_for_timeout(600)
        self.assertEqual(page.locator('#matrix-browse-assistant').get_attribute('data-state'),'paused')
        self.assertEqual(page.locator('#feed').evaluate('(e)=>e.scrollTop'),0)

    def test_warning_inside_comment_is_not_platform_notice(self):
        page = self.page()
        page.evaluate("document.body.insertAdjacentHTML('beforeend','<article class=\"comment-item\">我收到區域性顯示調整</article>')")
        self.start(page)

    def test_creator_diagnostics_reports_failed_images_and_offscreen_button(self):
        page = self.page(creator=True)
        page.evaluate("document.body.insertAdjacentHTML('beforeend','<img src=\"broken-image\" style=\"width:50px;height:50px\"><button style=\"position:absolute;top:2000px\">发布</button>')")
        page.wait_for_function('document.images[0].complete')
        page.locator('#matrix-browse-assistant').locator('#check').click()
        text = page.locator('#matrix-browse-assistant').locator('#status').inner_text()
        self.assertIn('图片失败 1',text); self.assertIn('完整可见 0',text)
        self.assertFalse(page.locator('#matrix-browse-assistant').locator('#plan').is_visible())

    def test_collect_and_browse_cannot_scroll_same_page_together(self):
        page = self.page(); self.start(page)
        page.locator('#matrix-browser-collector').locator('#start').click()
        page.wait_for_timeout(200)
        self.assertIn('浏览计划正在使用页面',page.locator('#matrix-browser-collector').locator('#status').inner_text())


if __name__ == '__main__': unittest.main()
