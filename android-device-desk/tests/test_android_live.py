import base64,struct,threading,time,unittest
from unittest.mock import patch,Mock
from app.webapp import Api
from types import SimpleNamespace
from app.android_live import LivePhone
from app.android_control import AndroidController

class Socket:
    def __init__(self):self.packets=[]
    def sendall(self,value):self.packets.append(value)
    def shutdown(self,*args):pass
    def close(self):pass

class LiveTests(unittest.TestCase):
    def setUp(self):
        controller=SimpleNamespace(_point=AndroidController._point,_lock=threading.RLock(),_reservations={},_adb=lambda *a,**k:b'')
        self.live=LivePhone(controller);self.live.token='selected-device-token';self.live.control=Socket();self.live.latest={'sequence':1,'width':332,'height':720,'time':time.monotonic(),'b64':'one-frame'}
        self.size={'width':332,'height':720}
    def test_touch_has_video_coords_and_up_releases(self):
        self.live.input(self.live.token,'down',{**self.size,'x':.5,'y':.5});self.live.input(self.live.token,'up',{**self.size,'x':.5,'y':.5})
        down,up=self.live.control.packets
        self.assertEqual(len(down),32);fields=struct.unpack('>BBQiiHHHII',down)
        self.assertEqual(fields[:3],(2,0,0xfffffffffffffffe));self.assertEqual(fields[5:7],(332,720));self.assertFalse(self.live.touching)
        self.assertEqual(up[1],1)
    def test_old_token_cannot_touch_or_stop_new_phone(self):
        with self.assertRaises(ValueError):self.live.input('previous-phone','home',self.size)
        self.assertFalse(self.live.stop('previous-phone')['stopped']);self.assertEqual(self.live.token,'selected-device-token');self.assertEqual(self.live.control.packets,[])
    def test_frame_size_changed_disallows_old_coordinates(self):
        with self.assertRaises(ValueError):self.live.input(self.live.token,'down',{'width':720,'height':332,'x':.2,'y':.2})
        self.assertEqual(self.live.control.packets,[])
    def test_clipboard_exact_unicode_and_newlines(self):
        text='标题：美业 😊\n正文 保留原文';self.live.input(self.live.token,'paste',{**self.size,'text':text})
        packet=self.live.control.packets[0];kind,sequence,paste,length=struct.unpack('>BQBI',packet[:14])
        self.assertEqual((kind,sequence,paste),(9,0,1));self.assertEqual(length,len(text.encode()));self.assertEqual(packet[14:].decode(),text)
    def test_invalid_paste_and_actions_send_nothing(self):
        for text in ('',None,'a'*65537):
            with self.assertRaises(ValueError):self.live.input(self.live.token,'paste',{**self.size,'text':text})
        with self.assertRaises(ValueError):self.live.input(self.live.token,'send-all',self.size)
        self.assertEqual(self.live.control.packets,[])
    def test_latest_frame_only_and_unchanged_frame_not_transferred(self):
        self.assertEqual(self.live.frame(self.live.token,1)['b64'],'');self.assertEqual(self.live.frame(self.live.token,0)['b64'],'one-frame')
    def test_stop_releases_touch(self):
        socket=self.live.control;self.live.input(self.live.token,'down',{**self.size,'x':.1,'y':.2});self.live.stop();self.assertEqual(socket.packets[-1][1],1);self.assertFalse(self.live.token)
    def test_explicit_clipboard_button_preserves_unicode_without_returning_content(self):
        api=Api();api._live=self.live
        clipboard=Mock();clipboard.stringForType_.return_value='电脑中文 😊\n保留换行'
        module=SimpleNamespace(NSPasteboard=SimpleNamespace(generalPasteboard=lambda:clipboard),NSPasteboardTypeString='text')
        with patch.dict('sys.modules',{'AppKit':module}):
            result=api.android_live_paste_clipboard(self.live.token,332,720)
        self.assertTrue(result['ok']);self.assertNotIn('text',result)
        self.assertEqual(self.live.control.packets[-1][14:].decode(),'电脑中文 😊\n保留换行')

    def test_stale_frame_blocks_actions(self):
        self.live.latest['time']=time.monotonic()-4
        with self.assertRaises(ValueError):self.live.input(self.live.token,'home',self.size)
        self.assertEqual(self.live.control.packets,[])

if __name__=='__main__':unittest.main()
