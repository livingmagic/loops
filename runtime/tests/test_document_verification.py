import unittest

from loops_runtime.document_verification import pointer, verify_document


class DocumentVerificationTests(unittest.TestCase):
    source = '# 交接明细\n\n东仓：73 箱。\n西仓：29 箱。\n\n## 责任\n王敏负责周三核对。\n\n来源：https://records.example.org/a\n'
    body = '东仓：73 箱。\n西仓：29 箱。\n责任\n王敏负责周三核对。\n来源：https://records.example.org/a'

    def check(self, actual, **options):
        return verify_document(self.source, actual, expected_title='发布报告', **options)['matched']

    def test_declared_title_role_supports_metadata_prefix_or_replacement_in_text_and_html(self):
        self.assertTrue(self.check('发布报告' + self.body, title_policy='target'))
        self.assertTrue(self.check('发布报告\n交接明细\n' + self.body, title_policy='target'))
        self.assertTrue(self.check('发布报告\n交接明细\n' + self.body, title_policy='preserve'))
        html = '<article><h1>发布报告</h1><p>东仓：73 箱。</p><p>西仓：29 箱。</p><h2>责任</h2><p>王敏负责周三核对。</p><p>来源：https://records.example.org/a</p></article>'
        self.assertTrue(self.check(html, actual_format='html', title_policy='target', actual_title='发布报告'))
        self.assertFalse(self.check(html, actual_format='html', title_policy='preserve'))
        self.assertFalse(self.check('发布报告' + self.body))

    def test_numbers_relations_omissions_extras_sources_and_body_headings_are_never_ignored(self):
        good = '发布报告\n' + self.body
        bad = [good.replace('73', '74'), good.replace('73', '29').replace('29 箱。\n责任', '73 箱。\n责任'),
               good.replace('王敏负责周三核对。', ''), good + '\n另有一段平台内容。',
               good.replace('https://records.example.org/a', ''), good.replace('/a', '/b'),
               good.replace('责任', '范围'), good.replace('73', '7 3')]
        for value in bad:
            with self.subTest(value=value):
                self.assertFalse(self.check(value, title_policy='target'))
        with self.assertRaisesRegex(ValueError, '名称'):
            self.check(good, actual_title='别的报告', title_policy='target')

    def test_only_structural_document_heading_can_change(self):
        source = '第一行是业务资料，并非文档级标题。\n记录：16。'
        self.assertFalse(verify_document(source, '发布报告\n记录：16。', expected_format='text', expected_title='发布报告', title_policy='target')['matched'])
        self.assertFalse(self.check('其他标题\n' + self.body, title_policy='target'))
        self.assertTrue(self.check('交接明细\n' + self.body))

    def test_markup_link_destinations_and_image_references_remain_content(self):
        source = '# 原标题\n\nSee **handover** [source](https://records.example.org/a).'
        html = '<h1>原标题</h1><p>See <strong>handover</strong> <a href="https://records.example.org/a">source</a>.</p>'
        self.assertTrue(verify_document(source, html, actual_format='html')['matched'])
        self.assertFalse(verify_document(source, html.replace('/a', '/b'), actual_format='html')['matched'])
        self.assertFalse(verify_document(source, html.replace(' href="https://records.example.org/a"', ''), actual_format='html')['matched'])
        image = '![diagram](https://records.example.org/image.png)'
        self.assertTrue(verify_document(image, '<img alt="diagram" src="https://records.example.org/image.png">', actual_format='html')['matched'])
        self.assertFalse(verify_document(image, 'diagram')['matched'])

    def test_unknown_representations_do_not_guess_or_silently_drop_meaning(self):
        for fmt in ('docx', '', None):
            with self.assertRaisesRegex(ValueError, '格式'):
                self.check(self.body, actual_format=fmt)
        with self.assertRaisesRegex(ValueError, '无法可靠'):
            self.check('<p><del>已完成</del></p>', actual_format='html')
        with self.assertRaisesRegex(ValueError, '标题'):
            verify_document(self.source, self.body, title_policy='target')

    def test_code_table_cells_and_list_structure_are_substantive_content(self):
        code = '```python\nif ready:\n    send()\n```\n'
        html = '<html><head><title>File name</title><style>ignored</style></head><body><pre><code>if ready:\n    send()\n</code></pre></body></html>'
        self.assertTrue(verify_document(code, html, actual_format='html')['matched'])
        self.assertFalse(verify_document(code, html.replace('    send()', 'send()'), actual_format='html')['matched'])
        self.assertFalse(verify_document(code, 'if ready:\n    send()')['matched'])
        table = '| 地区 | 数量 |\n|---|---|\n| 北京 | 73 |\n'
        rendered = '<table><tr><th>地区</th><th>数量</th></tr><tr><td>北京</td><td>73</td></tr></table>'
        self.assertTrue(verify_document(table, rendered, actual_format='html')['matched'])
        self.assertFalse(verify_document(table, rendered.replace('<td>北京</td>', '<td>北</td><td>京</td>'), actual_format='html')['matched'])
        items = '1. First\n2. Second\n'
        rendered = '<ol><li>First</li><li>Second</li></ol>'
        self.assertTrue(verify_document(items, rendered, actual_format='html')['matched'])
        self.assertFalse(verify_document(items, rendered.replace('<ol>', '<ol start="2">'), actual_format='html')['matched'])
        self.assertFalse(verify_document(items, rendered.replace('<li>First', '<li value="3">First'), actual_format='html')['matched'])

    def test_uri_boundary_survives_plain_text_line_wrap_removal_without_splitting_identifiers(self):
        source = '# 原标题\n\n合计：8\n\nhttps://records.example.org/a\n'
        actual = '发布报告合计：8https://records.example.org/a'
        self.assertTrue(verify_document(source, actual, expected_title='发布报告', title_policy='target')['matched'])
        self.assertFalse(verify_document(source, actual.replace('8https', '9https'), expected_title='发布报告', title_policy='target')['matched'])
        self.assertFalse(verify_document('ID: AB73', 'ID: AB 73', expected_format='text')['matched'])

    def test_json_pointer_uses_only_explicit_paths(self):
        self.assertEqual(pointer({'a/b': [{'c~d': 'value'}]}, '/a~1b/0/c~0d'), 'value')
        with self.assertRaisesRegex(ValueError, '指定字段'):
            pointer({'content': 'present'}, '/other')

    def test_absent_name_requirement_does_not_compare_metadata_to_none(self):
        self.assertTrue(verify_document(self.source, '交接明细\n' + self.body, actual_title='存储对象名称')['matched'])
        self.assertFalse(verify_document(self.source, '另一个标题\n' + self.body, actual_title='存储对象名称')['matched'])
