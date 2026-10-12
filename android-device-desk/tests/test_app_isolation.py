from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from app.webapp import Api


class IndependentAppTests(unittest.TestCase):
    def test_device_api_has_separate_storage_and_no_matrix_account_or_publish_api(self):
        with tempfile.TemporaryDirectory() as folder:
            api = Api(str(Path(folder)/'device.db'))
            self.assertEqual(Path(api.db_path).name, 'device.db')
            for method in ('hub_accounts','accounts_list','publish_start','messages_send','dy_scan_video'):
                self.assertFalse(hasattr(api, method))
            controller = Mock()
            controller.devices.return_value=[]
            api._android=controller
            self.assertEqual(api.android_devices(), {'ok':True,'items':[]})
            api.close()
            controller.close.assert_called_once()
