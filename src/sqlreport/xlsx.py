# sqlreport - 轻量 SQL 报表工具
# Copyright (c) 2026 zetsubouk
# SPDX-License-Identifier: MIT

"""手写最小 xlsx 写出器（zip + inlineStr），零依赖（PLAN.md P2-3 首选方案）。

sheets: [{"name", "columns", "rows"}] → xlsx 写入文件对象。
单元格按 Python 类型分型：int/float（非 bool）为数值单元格 <v>，
其余为内联字符串（XML 转义）；None 与空串不输出单元格。
调用方负责把数值列先转为 float/int（见 server._export 的 _to_num 集成）。
"""
import zipfile
from xml.sax.saxutils import escape

_CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
       '<Default Extension="xml" ContentType="application/xml"/>'
       '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
       '{SHEETS}'
       '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
       '</Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
         '<Relationship Id="rId0" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
         '</Relationships>')
_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<fonts count="1"><font/></fonts><fills count="2"><fill><patternFill patternType="none"/></fill>'
           '<fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders>'
           '<cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="1"><xf/></cellXfs></styleSheet>')

_BAD_SHEET = set('\\/*?:[]')


def _letter(n):
    """0-based 列号 → A/B/.../AA"""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet_name(name, used):
    """sheet 名清洗：非法字符换 _、截断 31 字符、重名追加 -2/-3 后缀。"""
    s = "".join("_" if c in _BAD_SHEET else c for c in str(name or "Sheet"))[:31] or "Sheet"
    base, k = s, 1
    while s in used:
        k += 1
        s = f"{base[:28]}-{k}"
    used.add(s)
    return s


def _cell(ref, v):
    """单元格 → XML 片段；数值（非 bool）为 <v>，字符串内联，None/空串为空。"""
    if v is None or v == "":
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f'<c r="{ref}"><v>{v}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'


def write_xlsx(sheets, fp):
    """sheets: [{"name", "columns", "rows"}] → xlsx 写入文件对象。
    zip 条目顺序固定（ContentTypes → rels → workbook → workbook.rels → styles → sheets），保证可测。"""
    used = set()
    names = [_sheet_name(s.get("name"), used) for s in sheets]
    wb_sheets = "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                        for i, n in enumerate(names))
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          f'<sheets>{wb_sheets}</sheets></workbook>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
               + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               + '</Relationships>')
    ct = _CT.replace("{SHEETS}", "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(len(sheets))))
    with zipfile.ZipFile(fp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", _STYLES)
        for i, s in enumerate(sheets):
            cols = list(s.get("columns") or [])
            rows = list(s.get("rows") or [])
            head = "".join(_cell(f"{_letter(j)}1", c) for j, c in enumerate(cols))
            body = "".join("<row r=\"%d\">" % (r + 2) + "".join(
                _cell(f"{_letter(j)}{r + 2}", v) for j, v in enumerate(row)) + "</row>"
                for r, row in enumerate(rows))
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       f'<sheetData><row r="1">{head}</row>{body}</sheetData></worksheet>')
