import unittest
from types import SimpleNamespace
from playwright.sync_api import sync_playwright
from app import browser_workspace as view


class BrowserWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start();cls.browser=cls.pw.chromium.launch(headless=True)
    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()
    def setUp(self):
        self.ctx=self.browser.new_context(viewport={'width':800,'height':600})
        self.ctx.route('**/*',lambda route:route.fulfill(content_type='text/html',body='''<button onclick="window.clicks=(window.clicks||0)+1" style="position:absolute;left:100px;top:100px;width:100px;height:60px">Local button</button><input id="entry" style="position:absolute;left:100px;top:200px;width:200px;height:50px"><div style="height:2000px"></div>'''))
        self.page=self.ctx.new_page();self.page.goto('https://www.rednote.com/test-fixture')
        self.token=view.capture(self.ctx)['items'][0]['token']
    def tearDown(self):self.ctx.close()
    def frame(self):return view.capture(self.ctx,self.token)['items'][0]['frame']
    def test_one_click_consumes_frame_and_never_replays(self):
        frame=self.frame();view.manual_input(self.ctx,self.token,frame,'click',{'x':.1875,'y':.2})
        self.assertEqual(self.page.evaluate('window.clicks'),1)
        with self.assertRaisesRegex(ValueError,'画面已经变化'):view.manual_input(self.ctx,self.token,frame,'click',{'x':.1875,'y':.2})
        self.assertEqual(self.page.evaluate('window.clicks'),1)
    def test_unicode_input_targets_only_current_focused_field(self):
        self.page.locator('#entry').focus();view.manual_input(self.ctx,self.token,self.frame(),'text',{'text':'美业文案 😊\n原文'})
        self.assertIn('美业文案 😊',self.page.locator('#entry').input_value())
    def test_resize_rejects_old_coordinates(self):
        frame=self.frame();self.page.set_viewport_size({'width':900,'height':700})
        with self.assertRaisesRegex(ValueError,'画面已经变化'):view.manual_input(self.ctx,self.token,frame,'click',{'x':.2,'y':.2})
    def test_closed_page_does_not_redirect_to_another_tab(self):
        self.ctx.new_page();self.page.close()
        with self.assertRaisesRegex(ValueError,'已关闭'):view.capture(self.ctx,self.token)
    def test_token_cannot_cross_account_context(self):
        other=self.browser.new_context()
        try:
            other.new_page()
            with self.assertRaisesRegex(ValueError,'已关闭'):view.capture(other,self.token)
        finally:other.close()
    def test_bad_or_nonfinite_coords_are_not_dispatched(self):
        for x in (-1,True,float('nan'),2):
            with self.assertRaises(ValueError):view.manual_input(self.ctx,self.token,self.frame(),'click',{'x':x,'y':.2})
        self.assertIsNone(self.page.evaluate('window.clicks'))
    def test_platform_warning_blocks_input(self):
        self.page.locator('body').evaluate("el=>el.innerHTML='账号异常提醒 不常见的浏览行为'")
        with self.assertRaises(ValueError):view.manual_input(self.ctx,self.token,self.frame(),'click',{'x':.2,'y':.2})
    def test_no_browser_context_or_page_is_created_by_capture(self):
        before=len(self.ctx.pages);view.capture(self.ctx);view.capture(self.ctx,self.token)
        self.assertEqual(len(self.ctx.pages),before)

if __name__=='__main__':unittest.main()
