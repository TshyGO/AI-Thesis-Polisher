import logging
import re

import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment

class ExcelExporter:
    """
    将 AI 润色建议导出为多 Sheet 的 Excel 表格。
    - 每章一个 Sheet，方便按章阅读
    - 包含句子编号、优先级、润色后完整句等详细字段
    支持颜色标注：绿色（保留）/ 黄色（驳回）
    """
    def __init__(self):
        self.logger = logging.getLogger("ExcelExporter")
        self.HEADER = ["段落号", "句子", "原句 (旧)", "润色理由", "修改片段(原)", "修改片段(改)", "润色后完整句", "状态", "优先级"]
        self.COL_WIDTHS = [8, 6, 50, 25, 25, 25, 50, 10, 9]

        self.FILL_HEADER = PatternFill("solid", fgColor="1F4E79")
        self.FILL_KEPT   = PatternFill("solid", fgColor="D4EDDA")  # 绿色 (保留)
        self.FILL_REJ    = PatternFill("solid", fgColor="FFF3CD")  # 黄色 (驳回)
        self.FONT_HEADER = Font(color="FFFFFF", bold=True)
        self.ALIGN_TOP   = Alignment(wrap_text=True, vertical="top")

    def _apply_header(self, ws):
        ws.append(self.HEADER)
        for cell in ws[1]:
            cell.fill = self.FILL_HEADER
            cell.font = self.FONT_HEADER
            cell.alignment = self.ALIGN_TOP
        ws.freeze_panes = "A2"
        col_letters = "ABCDEFGHI"
        for i, width in enumerate(self.COL_WIDTHS):
            if i < len(col_letters):
                ws.column_dimensions[col_letters[i]].width = width

    def _write_row(self, ws, item):
        """写入一行并着色"""
        # 构造"润色后完整句"：用新片段替换原句中的旧片段
        original_sentence = item.get("sentence", "")
        old_frag = item.get("old", "")
        new_frag = item.get("new", "")
        if original_sentence and old_frag and old_frag in original_sentence:
            polished = original_sentence.replace(old_frag, new_frag, 1)
        elif old_frag:
            polished = f"[改: {old_frag} → {new_frag}]"
        else:
            polished = ""

        row_data = [
            item.get("paragraph_idx", ""),
            item.get("sentence_id", ""),
            original_sentence or old_frag,
            item.get("reason", ""),
            old_frag,
            new_frag,
            polished,
            item.get("status", ""),
            item.get("priority", ""),
        ]
        ws.append(row_data)
        row_num = ws.max_row
        status = row_data[7]
        fill = self.FILL_KEPT if status == "✅保留" else (self.FILL_REJ if status == "❌驳回" else None)
        for cell in ws[row_num]:
            if fill:
                cell.fill = fill
            cell.alignment = self.ALIGN_TOP

    @staticmethod
    def _make_sheet_name(raw_name: str, existing_names: set) -> str:
        cleaned = re.sub(r"[\\/*?:\[\]]", "_", (raw_name or "未命名章节")).strip()
        if not cleaned:
            cleaned = "未命名章节"

        cleaned = cleaned[:31]
        candidate = cleaned
        suffix = 2
        while candidate in existing_names:
            suffix_text = f"_{suffix}"
            candidate = f"{cleaned[:31 - len(suffix_text)]}{suffix_text}"
            suffix += 1
        return candidate

    def export(self, export_path: str, records: list, chapters: list = None):
        """
        records 格式:
        [
            {
               "paragraph_idx": 15,
               "sentence_id": "S2",          # 句子编号（可选）
               "sentence": "原始完整句子",    # 完整原句（可选）
               "old": "被广泛应用",
               "new": "应用广泛",
               "reason": "更加顺口",
               "status": "✅保留",
               "priority": "高",             # 可选
               "chapter": "第2章_方法"       # 可选，用于分 Sheet
            }, ...
        ]
        chapters: 章节列表，用于建立 Sheet 映射
        """
        wb = openpyxl.Workbook()
        wb.remove(wb.active)  # 移除默认 Sheet

        # 按章节分组
        chapter_map = {}  # chapter_name -> [records]
        if chapters:
            for chapter in chapters:
                chapter_map.setdefault(chapter.get("name", "未命名章节"), [])
        for rec in records:
            ch = rec.get("chapter", "全局")
            chapter_map.setdefault(ch, []).append(rec)

        # 如果只有一个"全局"分组且 records 不为空，保留原始无 chapter 的兼容模式
        if not chapter_map:
            ws = wb.create_sheet("润色审核明细")
            self._apply_header(ws)
        else:
            used_sheet_names = set()
            for ch_name, ch_records in chapter_map.items():
                sheet_name = self._make_sheet_name(ch_name, used_sheet_names)
                used_sheet_names.add(sheet_name)
                ws = wb.create_sheet(sheet_name)
                self._apply_header(ws)
                for item in ch_records:
                    self._write_row(ws, item)

        # 若 Workbook 为空（无任何 sheet），加一个空 sheet 防止 save 报错
        if not wb.sheetnames:
            wb.create_sheet("无数据")

        try:
            wb.save(export_path)
            self.logger.info(f"成功导出 Excel 报表: {export_path}")
            return True
        except Exception as e:
            self.logger.error(f"导出 Excel 失败: {e}")
            return False
