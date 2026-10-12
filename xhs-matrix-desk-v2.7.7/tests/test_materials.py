import copy
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from app import materials


class MaterialsTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        materials.ensure(self.conn)
        self.result = {'url': 'https://www.douyin.com/video/test', 'title': '中文标题',
            'markdown': '# 正文\n价格与说明 100%_value', 'engine': 'crawl4ai',
            'version': '0.9.4', 'selector': 'main',
            'links': [{'url': 'https://example.test/', 'text': '参考'}],
            'metadata': {'description': '本地内容'}}

    def tearDown(self):
        self.conn.close()

    def test_identical_content_skips_duplicate_and_changes_get_new_version(self):
        first = materials.archive(self.conn, 'douyin', 1, self.result)
        duplicate = materials.archive(self.conn, 'douyin', 1, copy.deepcopy(self.result))
        self.assertTrue(first['added'])
        self.assertEqual(duplicate, {'id': first['id'], 'added': False})
        changed = {**self.result, 'markdown': '内容已更新'}
        revision = materials.archive(self.conn, 'douyin', 1, changed)
        self.assertTrue(revision['added'])
        self.assertNotEqual(revision['id'], first['id'])
        self.assertEqual(len(materials.list_materials(self.conn)), 2)

    def test_platform_and_account_id_are_separate_scopes(self):
        ids = [materials.archive(self.conn, platform, aid, self.result)['id']
            for platform, aid in [('douyin', 1), ('douyin', 2), ('xiaohongshu', 1), ('channels', 1)]]
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(len(materials.list_materials(self.conn, platform='douyin')), 2)

    def test_list_is_small_and_details_preserve_markdown_links_metadata(self):
        mid = materials.archive(self.conn, 'douyin', 1, self.result)['id']
        item = materials.list_materials(self.conn)[0]
        self.assertNotIn('markdown', item)
        self.assertNotIn('links_json', item)
        self.assertEqual(item['character_count'], len(self.result['markdown']))
        details = materials.get_material(self.conn, mid)
        self.assertEqual(details['links'], self.result['links'])
        self.assertEqual(details['metadata'], self.result['metadata'])
        self.assertEqual(details['markdown'], self.result['markdown'])
        self.assertEqual(details['engine_version'], '0.9.4')
        with self.assertRaisesRegex(ValueError, '不存在'):
            materials.get_material(self.conn, 99999)

    def test_search_treats_sql_wildcards_as_text(self):
        materials.archive(self.conn, 'douyin', 1, self.result)
        self.assertEqual(len(materials.list_materials(self.conn, query='100%_')), 1)
        self.assertEqual(materials.list_materials(self.conn, query='%_%'), [])
        self.assertEqual(len(materials.list_materials(self.conn, query='中文标题')), 1)

    def test_bad_parameters_do_not_write(self):
        for platform, aid, data in [('invalid', 1, self.result), ('douyin', 0, self.result),
                ('douyin', True, self.result), ('douyin', 1, {**self.result, 'markdown': ' '}),
                ('douyin', 1, {**self.result, 'url': 'file:///private'})]:
            with self.assertRaises((ValueError, TypeError)):
                materials.archive(self.conn, platform, aid, data)
        self.assertEqual(materials.list_materials(self.conn), [])

    def test_same_material_from_parallel_connections_is_still_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'archive.db'
            with sqlite3.connect(path) as conn:
                materials.ensure(conn)
            barrier = threading.Barrier(2)
            results = []
            errors = []
            def save():
                conn = sqlite3.connect(path, timeout=5)
                try:
                    barrier.wait(timeout=5)
                    results.append(materials.archive(conn, 'douyin', 1, self.result))
                except Exception as exc:
                    errors.append(exc)
                finally:
                    conn.close()
            threads = [threading.Thread(target=save) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertEqual(errors, [])
            self.assertEqual(sum(row['added'] for row in results), 1)
            self.assertEqual(len({row['id'] for row in results}), 1)


if __name__ == '__main__':
    unittest.main()
