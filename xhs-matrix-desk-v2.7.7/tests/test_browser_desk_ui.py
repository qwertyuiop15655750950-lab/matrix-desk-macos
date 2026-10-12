"""Offline headless page tests; no production browser or platform request is touched."""
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright

BOOT='''
let curSection='account_hub';const hubItems=[{id:1,key:'xiaohongshu:1',platform:'xiaohongshu',nickname:'甲',running:true},{id:1,key:'douyin:1',platform:'douyin',nickname:'乙',running:true}];
const HUB_PLATFORMS={xiaohongshu:'小红书',douyin:'抖音'};
const hubKey=a=>a.key,hubVisibleItems=()=>hubItems,esc=v=>String(v||'');
const toast=()=>{},hubDraw=()=>{},hubGuard=f=>f;let showSection=async id=>{curSection=id};window.calls=[];
const hubCall=async(method,...args)=>{
 window.calls.push({method,args});
 if(method==='hub_browser_input')return{ok:true};
 if(window.delay&&method==='hub_browser_previews'&&args[0]==='xiaohongshu')return await new Promise(resolve=>window.resolveOld=()=>resolve({items:[{token:'a'.repeat(32),title:'old',image:window.image,frame:'old-frame'}]}));
 const token=args[0]==='xiaohongshu'?'a'.repeat(32):'b'.repeat(32);
 return{items:[{token,title:args[0],image:window.image,frame:'frame-'+token}]};
};
window.image='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQ0AAAAASUVORK5CYII=';
'''
class BrowserDeskUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.pw=sync_playwright().start();cls.browser=cls.pw.chromium.launch(headless=True)
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.pw.stop()
    def setUp(self):
        self.page=self.browser.new_page();self.page.set_content('<div id="page"></div>');self.page.add_script_tag(content=BOOT);self.page.add_script_tag(content=Path('app/ui/browser_desk.js').read_text());self.page.evaluate('browserDeskOpen()')
    def tearDown(self):self.page.close()
    def test_switch_then_manual_text_targets_selected_platform_and_id(self):
        self.page.evaluate("browserDeskChooseAccount('douyin:1')");self.page.wait_for_function('browserDeskFrame!==null')
        self.page.locator('#bd-text').fill('人工输入');self.page.locator('#bd-insert').click();self.page.wait_for_function("window.calls.some(c=>c.method==='hub_browser_input')")
        call=self.page.evaluate("window.calls.find(c=>c.method==='hub_browser_input')")
        self.assertEqual(call['args'][:3],['douyin',1,'b'*32]);self.assertEqual(call['args'][4],'text');self.assertEqual(call['args'][5],{'text':'人工输入'})
    def test_late_capture_cannot_switch_back_to_old_account(self):
        self.page.evaluate("window.delay=true;browserDeskChooseAccount('xiaohongshu:1');void 0")
        self.page.evaluate("browserDeskChooseAccount('douyin:1')");self.page.evaluate('window.resolveOld()');self.assertEqual(self.page.evaluate('browserDeskSelection.key'),'douyin:1')
        self.assertIn('乙',self.page.locator('#bd-status').inner_text())
    def test_navigation_stops_refresh_and_clears_frame_memory(self):
        self.page.evaluate("browserDeskChooseAccount('douyin:1')");self.page.evaluate("showSection('publish')");self.assertFalse(self.page.evaluate('browserDeskActive'));self.assertEqual(self.page.evaluate('browserDeskCache.size'),0);self.assertIsNone(self.page.evaluate('browserDeskFrame'))
    def test_overview_never_launches_browser(self):
        self.assertFalse(self.page.evaluate("window.calls.some(c=>c.method==='hub_open')"));self.assertEqual(self.page.locator('.browser-preview').count(),2)

if __name__=='__main__':unittest.main()
