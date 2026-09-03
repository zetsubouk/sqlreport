#!/usr/bin/env python3
"""xlsx.py 单元测试：zip 结构与单元格编码"""
import io, os, sys, unittest, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from sqlreport.xlsx import write_xlsx

class TestXlsx(unittest.TestCase):
    def test_zip_structure_and_cells(self):
        buf = io.BytesIO()
        write_xlsx([{"name": "数据", "columns": ["名", "值"],
                     "rows": [["<b>", 1.5], ["x", "y"]]}], buf)
        z = zipfile.ZipFile(buf)
        names = set(z.namelist())
        self.assertIn("[Content_Types].xml", names)
        self.assertIn("xl/workbook.xml", names)
        self.assertIn("xl/worksheets/sheet1.xml", names)
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('t="inlineStr"', sheet)          # 字符串内联
        self.assertIn("&lt;b&gt;", sheet)                # XML 转义
        self.assertNotIn('t="s"', sheet)                 # 不用共享字符串表
        self.assertIn("<v>1.5</v>", sheet)               # 数值单元格

    def test_sheet_name_sanitized(self):
        buf = io.BytesIO()
        write_xlsx([{"name": "a/b:c*d?e[f]g" + "x" * 40, "columns": ["c"], "rows": []}], buf)
        wb = zipfile.ZipFile(buf).read("xl/workbook.xml").decode()
        self.assertNotIn("/", wb.split('name="')[1].split('"')[0])

    def test_multi_sheets(self):
        buf = io.BytesIO()
        write_xlsx([{"name": "摘要", "columns": ["指标", "值"], "rows": [["合计", 350.5]]},
                    {"name": "数据", "columns": ["区域"], "rows": [["华东"]]}], buf)
        z = zipfile.ZipFile(buf)
        wb = z.read("xl/workbook.xml").decode()
        self.assertIn('name="摘要"', wb)
        self.assertIn('name="数据"', wb)
        self.assertIn("xl/worksheets/sheet2.xml", set(z.namelist()))

    def test_duplicate_sheet_names_deduped(self):
        buf = io.BytesIO()
        write_xlsx([{"name": "数据", "columns": ["a"], "rows": []},
                    {"name": "数据", "columns": ["a"], "rows": []}], buf)
        wb = zipfile.ZipFile(buf).read("xl/workbook.xml").decode()
        self.assertIn('name="数据-2"', wb)

    def test_none_and_empty_cells_skipped(self):
        buf = io.BytesIO()
        write_xlsx([{"name": "s", "columns": ["a", "b"],
                     "rows": [[None, ""], ["x", 0]]}], buf)
        sheet = zipfile.ZipFile(buf).read("xl/worksheets/sheet1.xml").decode()
        self.assertNotIn('r="A2"', sheet)   # None 单元格不输出
        self.assertNotIn('r="B2"', sheet)   # 空串单元格不输出
        self.assertIn("<v>0</v>", sheet)    # 0 是合法数值

    def test_bool_and_string_numbers_stay_text(self):
        # bool 不是数值单元格（防 Excel 误读 TRUE）；字符串数字保持文本（转换职责在调用方）
        buf = io.BytesIO()
        write_xlsx([{"name": "s", "columns": ["a", "b", "c"],
                     "rows": [[True, "123", 12]]}], buf)
        sheet = zipfile.ZipFile(buf).read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("True", sheet)
        self.assertIn(">123<", sheet)
        self.assertIn("<v>12</v>", sheet)


if __name__ == "__main__":
    unittest.main()
