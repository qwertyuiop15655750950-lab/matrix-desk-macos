"""Offline DOM interaction tests for real-time phone controls; no ADB is invoked."""
import unittest,io,base64
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright
BOOT='''
let curSection='android_control';const esc=v=>String(v||''),toast=()=>{},wallStop=()=>{},wallMount=()=>{};let showSection=async id=>{curSection=id};
const wallVisible=()=>[{serial:'one',model:'测试机1'},{serial:'two',model:'测试机2'}];window.calls=[];let sequence=0;
const androidCall=async(method,...args)=>{
 window.calls.push({method,args});
 if(method==='android_live_start'){
  if(window.delayStart)return await new Promise(resolve=>window.resolveStart=()=>resolve({token:'old-session'}));
  return{token:'session-'+args[0]};
 }
 if(method==='android_live_frame')return{ready:true,sequence:++sequence,width:332,height:720,b64:window.frame,frame_age_ms:10};
 if(method==='android_live_input'&&args[1]==='down'&&window.delayDown)return await new Promise(resolve=>window.resolveDown=()=>resolve({accepted:true}));
 return{accepted:true,stopped:true};
};
'''
class PhoneLiveUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.pw=sync_playwright().start();cls.browser=cls.pw.chromium.launch(headless=True)
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.pw.stop()
    def setUp(self):
        self.page=self.browser.new_page(viewport={'width':1100,'height':1000});self.page.set_content('<div id="phone-wall"></div><style>#live-image{width:166px;height:360px;display:block}</style>')
        image=Image.new('RGB',(332,720),'white');out=io.BytesIO();image.save(out,'JPEG');self.page.evaluate('value=>window.frame=value',base64.b64encode(out.getvalue()).decode())
        self.page.add_script_tag(content=BOOT);self.page.add_script_tag(content=Path('app/ui/phone_live.js').read_text())
    def tearDown(self):self.page.close()
    def open(self):self.page.evaluate('phoneLiveOpen(0)');self.page.wait_for_function('phoneLiveSize!==null')
    def test_click_delivers_one_down_up_pair(self):
        self.open();self.page.locator('#live-image').click();self.page.wait_for_function("window.calls.filter(c=>c.method==='android_live_input').length===2")
        calls=self.page.evaluate("window.calls.filter(c=>c.method==='android_live_input')")
        self.assertEqual([c['args'][1] for c in calls],['down','up']);self.assertTrue(all(c['args'][0]=='session-one' for c in calls))
    def test_release_before_delayed_down_completes_still_sends_up(self):
        self.open();self.page.evaluate('window.delayDown=true');self.page.locator('#live-image').click();self.page.evaluate('window.resolveDown()');self.page.wait_for_function("window.calls.some(c=>c.method==='android_live_input'&&c.args[1]==='up')")
        self.assertIsNone(self.page.evaluate('phoneLivePointer'))
    def test_clipboard_button_targets_selected_session_without_reading_in_ui(self):
        self.open();self.page.locator('#live-clipboard').click();call=self.page.evaluate("window.calls.find(c=>c.method==='android_live_paste_clipboard')")
        self.assertEqual(call['args'],['session-one',332,720])
    def test_closing_while_start_is_pending_closes_obsolete_session(self):
        self.page.evaluate('window.delayStart=true;phoneLiveOpen(0);void 0');self.page.wait_for_function('window.resolveStart!==undefined');self.page.evaluate('phoneLiveClose()');self.page.evaluate('window.resolveStart()')
        self.page.wait_for_function("window.calls.some(c=>c.method==='android_live_stop'&&c.args[0]==='old-session')")
        self.assertEqual(self.page.evaluate('phoneLiveToken'),'');self.assertEqual(self.page.locator('#phone-live').count(),0)
    def test_switching_phone_stops_old_stream_before_using_new(self):
        self.open();self.page.evaluate('phoneLiveOpen(1)');self.page.wait_for_function("phoneLiveToken==='session-two'")
        self.assertTrue(self.page.evaluate("window.calls.some(c=>c.method==='android_live_stop'&&c.args[0]==='session-one')"))

if __name__=='__main__':unittest.main()
