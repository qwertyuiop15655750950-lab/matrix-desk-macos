"""Import plain text fields from a DOCX. No macros, embedded links or instructions run."""
from pathlib import Path
import re
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
FIELD = re.compile(r'^(封面大字报|发布标题|正文|话题)\s*[:：]\s*(.*)$')
HEADING = re.compile(r'^\d{1,4}\s*[｜|、.．]\s*.+')
KEYS = {'封面大字报':'cover_text', '发布标题':'title', '正文':'body', '话题':'topics'}


def parse_docx(path):
    path = Path(path)
    if path.suffix.lower() != '.docx' or not path.is_file():
        raise ValueError('请选择 .docx 文档')
    if path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError('文档不能超过 5MB')
    try:
        with ZipFile(path) as archive:
            info = archive.getinfo('word/document.xml')
            if info.file_size > 10 * 1024 * 1024:
                raise ValueError('文档正文超过 10MB')
            source = archive.read(info)
        if b'<!DOCTYPE' in source or b'<!ENTITY' in source:
            raise ValueError('文档包含不支持的 XML 定义')
        root = ET.fromstring(source)
    except (BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError('无法读取 Word 文档，请另存为 .docx 后重试') from error
    entries, errors, current, field = [], [], {}, None

    def finish():
        if not current:
            return
        missing = [label for label,key in KEYS.items() if not current.get(key,'').strip()]
        if missing:
            errors.append(f"第 {len(entries)+len(errors)+1} 组缺少：{'、'.join(missing)}")
        else:
            entries.append({key:current[key].strip() for key in KEYS.values()})

    body = root.find(f'{W}body')
    if body is None:
        raise ValueError('文档没有正文')
    for paragraph in body.iter(f'{W}p'):
        # Preserve explicit line breaks within a field; ignore document styling.
        text = ''.join(node.text or '' if node.tag == f'{W}t' else '\n'
                       for node in paragraph.iter() if node.tag in (f'{W}t',f'{W}br')).strip()
        if not text:
            continue
        if HEADING.match(text):
            finish(); current = {}; field = None; continue
        matched = FIELD.match(text)
        if matched:
            key = KEYS[matched[1]]
            if key == 'cover_text' and key in current:
                finish(); current = {}
            field = key; current[key] = matched[2]; continue
        if field:
            # Section introductions outside the four fields are never instructions.
            if text.startswith('适合') and field == 'topics':
                field = None; continue
            current[field] = current.get(field,'') + '\n' + text
    finish()
    if len(entries) > 500:
        raise ValueError('单次最多导入 500 组文案')
    if errors:
        raise ValueError('；'.join(errors[:8]) + '。已取消整次导入，请补齐后重试')
    if not entries:
        raise ValueError('没有识别到文案；需要封面大字报、发布标题、正文、话题四个字段')
    return entries


def import_entries(conn, entries, now):
    added, duplicates = [], 0
    with conn:
        for entry in entries:
            exists = conn.execute('SELECT id FROM drafts WHERE title=? AND body=? AND topics=? AND cover_text=?',
                (entry['title'],entry['body'],entry['topics'],entry['cover_text'])).fetchone()
            if exists:
                duplicates += 1; continue
            cursor = conn.execute('INSERT INTO drafts(title,body,topics,cover_text,mode,template,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                (entry['title'],entry['body'],entry['topics'],entry['cover_text'],'poster','xhs_builtin',now,now))
            added.append(cursor.lastrowid)
    return {'added':len(added),'duplicates':duplicates,'ids':added,'total':len(entries)}
