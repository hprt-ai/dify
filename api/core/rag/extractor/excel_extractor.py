"""Abstract interface for document loader implementations."""

import os
from typing import Optional

import pandas as pd
from openpyxl import load_workbook  # type: ignore
from openpyxl.worksheet.worksheet import Worksheet

from core.rag.extractor.extractor_base import BaseExtractor
from core.rag.models.document import Document


class ExcelExtractor(BaseExtractor):
    """Load Excel files.


    Args:
        file_path: Path to the file to load.
    """

    def __init__(self, file_path: str, encoding: Optional[str] = None, autodetect_encoding: bool = False):
        """Initialize with file path."""
        self._file_path = file_path
        self._encoding = encoding
        self._autodetect_encoding = autodetect_encoding

    def _get_actual_max_column(self, sheet: Worksheet) -> int:
        """获取实际的最大列数（有数据的列）"""
        max_col = sheet.max_column
        
        # 从最后一列向前扫描，找到最后一个非空的单元格
        for col in range(max_col, 0, -1):
            # 检查前3行中的任意一行在这一列是否有值
            for row in range(1, 4):
                cell_value = sheet.cell(row=row, column=col).value
                if cell_value is not None and str(cell_value).strip() != '':
                    return col
        
        return max_col  # 如果没找到，返回原始的max_column

    def _analyze_merged_row(self, sheet: Worksheet, row_num: int, actual_max_col: int) -> tuple[bool, list[str], bool]:
        """分析某一行的合并单元格情况"""
        row_values = [''] * actual_max_col  # 预先填充空字符串
        is_fully_merged = False
        has_partial_merge = False
        
        # 获取这一行所有的合并单元格范围
        merged_ranges = [r for r in sheet.merged_cells.ranges if r.min_row <= row_num <= r.max_row]
        
        # 计算合并单元格的总覆盖率
        total_merged_cols = 0
        for merged_range in merged_ranges:
            if merged_range.min_row <= row_num <= merged_range.max_row:
                total_merged_cols += (merged_range.max_col - merged_range.min_col + 1)
        
        # 如果合并单元格覆盖率超过90%，视为全行合并
        if total_merged_cols / actual_max_col >= 0.9:
            is_fully_merged = True
        
        # 首先处理合并单元格
        for merged_range in merged_ranges:
            if merged_range.min_row <= row_num <= merged_range.max_row:
                if (merged_range.min_row == row_num and 
                    merged_range.min_col == 1 and 
                    merged_range.max_col == actual_max_col):
                    is_fully_merged = True
                    break
                elif merged_range.min_row == row_num and merged_range.min_col != merged_range.max_col:
                    has_partial_merge = True
                    # 获取合并单元格的值
                    merged_value = sheet.cell(
                        row=merged_range.min_row,
                        column=merged_range.min_col
                    ).value
                    merged_value = str(merged_value) if merged_value is not None else ''
                    # 将值应用到所有被合并的列
                    for col in range(merged_range.min_col - 1, min(merged_range.max_col, actual_max_col)):
                        row_values[col] = merged_value
        
        # 然后处理非合并单元格
        for col in range(actual_max_col):
            if not row_values[col]:  # 如果该位置还没有值（不在任何合并范围内）
                cell = sheet.cell(row=row_num, column=col + 1)
                value = cell.value
                row_values[col] = str(value) if value is not None else ''
            
        return is_fully_merged, row_values, has_partial_merge

    def _process_headers(self, sheet: Worksheet) -> tuple[list[str], int]:
        """处理表头，在前10行内找到表头结束位置"""
        actual_max_col = self._get_actual_max_column(sheet)
        final_headers = [''] * actual_max_col
        current_row = 1
        data_start_row = 1
        
        # 存储分组标题信息
        group_headers = [''] * actual_max_col
        has_group_headers = False
        
        while current_row <= 10:  # 最多检查前10行
            is_fully_merged, row_values, has_partial_merge = self._analyze_merged_row(
                sheet, current_row, actual_max_col
            )
            
            # 如果是第一行且全行合并，认为是标题行，跳过
            if current_row == 1 and is_fully_merged:
                current_row += 1
                data_start_row = current_row
                continue
                
            # 检查是否是分组标题行
            if has_partial_merge or is_fully_merged:
                # 更新分组标题
                last_group = ''
                for col_idx in range(actual_max_col):
                    if row_values[col_idx]:
                        last_group = row_values[col_idx]
                        group_headers[col_idx] = last_group
                    elif last_group:
                        group_headers[col_idx] = last_group
                has_group_headers = True
                current_row += 1
                data_start_row = current_row
                continue
            
            # 处理普通行
            has_values = False
            for col_idx in range(actual_max_col):
                current_val = row_values[col_idx]
                if current_val:
                    has_values = True
                    if has_group_headers and group_headers[col_idx] and group_headers[col_idx] != current_val:
                        # 如果有分组标题且与当前值不同，合并显示
                        final_headers[col_idx] = f"{group_headers[col_idx]}-{current_val}"
                    else:
                        # 如果没有分组标题或分组标题与当前值相同，直接使用当前值
                        final_headers[col_idx] = current_val
                elif not final_headers[col_idx] and group_headers[col_idx]:
                    # 如果当前列没有值但有分组标题，使用分组标题
                    final_headers[col_idx] = group_headers[col_idx]
            
            # 如果这一行没有任何值，可能是空行
            if not has_values:
                # 如果已经有了标题，说明到了数据行
                if any(header != '' for header in final_headers):
                    data_start_row = current_row
                    break
                # 如果还没有标题，继续检查下一行
                current_row += 1
                data_start_row = current_row
                continue
                
            # 如果这一行有值，检查是否所有必要的列都有了标题
            if all(header != '' for header in final_headers[:actual_max_col-2]):  # 允许最后两列为空
                current_row += 1
                data_start_row = current_row
                break
                
            current_row += 1
            data_start_row = current_row
        
        # 确保所有列都有表头
        for col_idx in range(actual_max_col):
            if not final_headers[col_idx] and group_headers[col_idx]:
                final_headers[col_idx] = group_headers[col_idx]
        
        return final_headers, data_start_row

    def extract(self) -> list[Document]:
        """Load from Excel file in xls or xlsx format using Pandas and openpyxl."""
        documents = []
        file_extension = os.path.splitext(self._file_path)[-1].lower()

        if file_extension == ".xlsx":
            wb = load_workbook(self._file_path, data_only=True)
            for sheet_name in wb.sheetnames:
                sheet = wb[sheet_name]
                
                # 使用表头处理逻辑
                headers, data_start_row = self._process_headers(sheet)
                
                # 从数据开始行开始处理数据
                for row_idx in range(data_start_row, sheet.max_row + 1):
                    row_data = {}
                    
                    # 处理每一列的数据
                    for col_idx, header in enumerate(headers):
                        if not header:  # 跳过没有表头的列
                            continue
                            
                        cell = sheet.cell(row=row_idx, column=col_idx + 1)
                        value = cell.value
                        
                        # 即使值为空也保留列名
                        if value is not None and str(value).strip() != '':
                            if cell.hyperlink:
                                value = f"[{value}]({cell.hyperlink.target})"
                        else:
                            value = ""
                            
                        row_data[header] = value
                    
                    # 确保所有表头都出现在输出中
                    page_content = []
                    for header in headers:
                        if header:  # 跳过空表头
                            value = row_data.get(header, "")
                            page_content.append(f'"{header}":"{value}"')
                            
                    documents.append(
                        Document(page_content=";".join(page_content), metadata={"source": self._file_path})
                    )

        elif file_extension == ".xls":
            excel_file = pd.ExcelFile(self._file_path, engine="xlrd")
            for excel_sheet_name in excel_file.sheet_names:
                df = excel_file.parse(sheet_name=excel_sheet_name)
                df.dropna(how="all", inplace=True)

                for _, row in df.iterrows():
                    row_data = {}
                    for k, v in row.items():
                        if pd.notna(v) and str(v).strip() != '':
                            row_data[k] = v
                        else:
                            row_data[k] = ""
                            
                    # 确保所有列都出现在输出中
                    page_content = []
                    for col in df.columns:
                        value = row_data.get(col, "")
                        page_content.append(f'"{col}":"{value}"')
                        
                    documents.append(
                        Document(page_content=";".join(page_content), metadata={"source": self._file_path})
                    )
        else:
            raise ValueError(f"Unsupported file extension: {file_extension}")

        return documents 
    
