"""Offline DOM checks for native selection and session handoff ordering."""
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
BOOT='''let curSection='account_hub',browserDeskActive=false;const hubItems=[{id:1,key:'xiaohongshu:1',platform:'xiaohongshu',nickname:'甲'},{id:2,key:'douyin:2',platform:'douyin',nickname:'乙'}];const HUB_PLATFORMS={xiaohongshu:'小红书',douyin:'抖音'};const hubKey=a=>a.key,hubVisibleItems=()=>hubItems,esc=v=>String(v||'');const toast=()=>{},hubDraw=()=>{},hubGuard=f=>f;async function browserDeskOpen(){}function browserDeskStop(){browserDeskActive=false;}function browserDeskChooseAccount(){}let showSection=async id=>{window.events.push('section:'+id);curSection=id};window.events=[];window.calls=[];async function hubCall(method,...args){window.calls.push({method,args});if(method==='native_browser_status')return {available:true,items:[]};if(window.delay&&method==='native_browser_select'&&args[0]==='xiaohongshu')return await new Promise(resolve=>window.oldResolve=()=>resolve({embedded:true}));if(method==='native_browser_hide')window.events.push('saved');return {embedded:true};}'''
class NativeUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.pw=sync_playwright().start();cls.browser=cls.pw.chromium.launch(headless=True)
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.pw.stop()
    def setUp(self):
        self.page=self.browser.new_page();self.page.set_content('<div id="page"></div><div id="modal-root"></div><div id="prompt-root"></div>');self.page.add_script_tag(content=BOOT);self.page.add_script_tag(content=Path('app/ui/native_browser.js').read_text());self.page.evaluate('browserDeskOpen()')
    def tearDown(self):self.page.close()
    def test_account_click_opens_even_without_chromium_running(self):
        self.page.locator('[data-native-account="douyin:2"]').click();self.page.wait_for_function("nativeBrowserSelected==='douyin:2'")
        self.assertEqual(self.page.evaluate("window.calls.find(c=>c.method==='native_browser_select').args.slice(0,2)"),['douyin',2]);self.assertEqual(self.page.locator('#bd-text').count(),0);self.assertEqual(self.page.locator('#bd-frame').count(),0)
    def test_old_open_result_cannot_replace_new_account(self):
        self.page.evaluate("window.delay=true;nativeBrowserChoose('xiaohongshu:1');void 0");self.page.evaluate("nativeBrowserChoose('douyin:2')");self.page.evaluate('window.oldResolve()');self.assertEqual(self.page.evaluate('nativeBrowserSelected'),'douyin:2');self.assertIn('乙',self.page.locator('#nb-status').inner_text())
    def test_navigation_flushes_sessions_before_opening_publish(self):
        self.page.evaluate("nativeBrowserChoose('douyin:2')");self.page.evaluate("showSection('publish')");self.assertEqual(self.page.evaluate('window.events'),['saved','section:publish']);self.assertFalse(self.page.evaluate('nativeBrowserActive'))
    def test_modal_hides_native_surface(self):
        self.page.evaluate("nativeBrowserChoose('douyin:2')");self.page.evaluate("document.querySelector('#modal-root').innerHTML='<div>test</div>';void 0");self.page.wait_for_function("window.calls.some(c=>c.method==='native_browser_layout'&&!c.args[2].visible)");self.assertFalse(self.page.evaluate('nativeBrowserRect().visible'))
    def test_overview_only_places_open_accounts_and_reenters_native_view(self):
        self.page.evaluate("const previousCall=hubCall;hubCall=async(method,...args)=>method==='native_browser_status'?{available:true,items:[{platform:'xiaohongshu',id:1},{platform:'douyin',id:2}]}:previousCall(method,...args)")
        self.page.evaluate('nativeBrowserShowOverview()')
        self.assertEqual(self.page.locator('[data-native-slot]').count(),2)
        slots=self.page.evaluate("window.calls.find(c=>c.method==='native_browser_overview').args[1]")
        self.assertEqual([(s['platform'],s['id']) for s in slots],[('xiaohongshu',1),('douyin',2)])
        self.assertFalse(self.page.evaluate("window.calls.some(c=>c.method==='native_browser_select')"))
        self.page.locator('[data-native-enter="douyin:2"]').click()
        self.page.wait_for_function("nativeBrowserSelected==='douyin:2'&&!nativeBrowserOverview")
    def test_overview_modal_hides_all_views(self):
        self.page.evaluate("const previousCall=hubCall;hubCall=async(method,...args)=>method==='native_browser_status'?{available:true,items:[{platform:'douyin',id:2}]}:previousCall(method,...args)")
        self.page.evaluate('nativeBrowserShowOverview()')
        self.page.evaluate("document.querySelector('#modal-root').innerHTML='<div>test</div>';void 0")
        self.page.wait_for_function("window.calls.some(c=>c.method==='native_browser_overview_layout'&&c.args[2].length===1&&!c.args[2][0].rect.visible)")
if __name__=='__main__':unittest.main()
