import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from app.native_browser import profile_identifier, validate_rect, NativeBrowser
from app.native_session import clean_state, load_state, save_state, apply_state

class NativeSessionTests(unittest.TestCase):
    def test_profiles_are_stable_and_platform_isolated(self):
        self.assertEqual(profile_identifier('/tmp/a','douyin',1),profile_identifier('/tmp/a','douyin',1))
        self.assertNotEqual(profile_identifier('/tmp/a','douyin',1),profile_identifier('/tmp/a','xiaohongshu',1))
        self.assertNotEqual(profile_identifier('/tmp/a','douyin',1),profile_identifier('/tmp/b','douyin',1))
    def test_rectangle_rejects_nonfinite_and_negative_size(self):
        for bad in (float('nan'),True,-1):
            with self.assertRaises(ValueError):validate_rect(dict(x=0,y=0,width=bad,height=20))
    def test_cookie_and_origin_boundaries(self):
        state=clean_state('xiaohongshu',{'cookies':[{'domain':'.rednote.com','name':'a','value':'local'}, {'domain':'rednote.com.evil.test'}], 'origins':[{'origin':'https://creator.rednote.com','localStorage':[]},{'origin':'https://www.douyin.com','localStorage':[]}]})
        self.assertEqual(len(state['cookies']),1);self.assertEqual(len(state['origins']),1)
    def test_private_atomic_handoff_never_crosses_accounts(self):
        with tempfile.TemporaryDirectory() as root:
            path=save_state(root,'douyin',1,{'cookies':[{'domain':'.douyin.com','name':'local-test','value':'local-test','path':'/'}]})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            self.assertEqual(load_state(root,'douyin',2),(None,None))
            ctx=Mock();ctx.pages=[];ctx._matrix_native_version=None
            ctx.cookies.return_value=[{'domain':'.douyin.com','name':'old','path':'/'},{'domain':'.rednote.com','name':'keep','path':'/'}]
            self.assertTrue(apply_state(ctx,root,'douyin',1));ctx.clear_cookies.assert_called_once_with(name='old',domain='.douyin.com',path='/')
            self.assertFalse(apply_state(ctx,root,'douyin',1));ctx.add_cookies.assert_called_once()
    def test_empty_handoff_propagates_logout(self):
        with tempfile.TemporaryDirectory() as root:
            save_state(root,'xiaohongshu',3,{'cookies':[],'origins':[]})
            ctx=Mock();ctx.pages=[];ctx.cookies.return_value=[{'domain':'.rednote.com','name':'old','path':'/'}];ctx._matrix_native_version=None
            self.assertTrue(apply_state(ctx,root,'xiaohongshu',3));ctx.clear_cookies.assert_called_once();ctx.add_cookies.assert_not_called()
    def test_old_selection_and_layout_cannot_reopen_after_leaving(self):
        manager=NativeBrowser(None,'/tmp',Mock());self.assertTrue(manager._intent(10,('douyin',1)))
        self.assertTrue(manager._intent(11,None));self.assertFalse(manager._intent(10,('douyin',1)))
        self.assertEqual(manager.layout(10,1,dict(x=0,y=0,width=10,height=10)),{'stale':True})

if __name__=='__main__':unittest.main()
