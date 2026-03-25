---
name: merge-scan-file
description: 合并双面扫描产生的两份 PDF（通常为奇数页与偶数页分开扫描）并输出按原始信件顺序排列的新文件。用户提供两个扫描 PDF、要求根据页脚 x/n 标记或按文件名序号穿插重排时使用。
---

# Merge Scan File

## Overview

接收两个 PDF 文件，优先识别页脚 `x/n`（如 `3/12`）并按 `x` 升序重排。
若无法稳定识别 `x/n`，则按文件名序号判断先后，按奇偶页穿插合并。

## 输入与输出

- 输入：两个 PDF 文件路径。
- 输出：新 PDF 文件，命名为 `Merged_X.pdf`。
  - `X` 从 `001` 开始递增。
  - 在输出目录中自动查找已有 `Merged_XXX.pdf`，生成下一个序号。

## 合并规则

1. 优先规则：页脚 `x/n`
- 在每页底部优先识别类似 `x/n` 的页码标记。
- 若两份 PDF 的所有页都能识别出有效 `x/n` 且 `x` 不重复，则按 `x` 从小到大合并。

2. 回退规则：文件名序号 + 奇偶穿插
- 若 `x/n` 规则不可用，则比较两个文件名中的数字序号。
- 序号较小的文件视作奇数页文件，序号较大的文件视作偶数页文件。
- 奇数页文件按原顺序读取（如 `1,3,5,7,9`）。
- 偶数页文件按倒序读取后再穿插（如源顺序为 `10,8,6,4,2`，则使用 `2,4,6,8,10`）。
- 最终交替合并输出 `1,2,3,4,...`（即奇页1、偶页1、奇页2、偶页2...）。

3. 空白页处理
- 若检测到完全空白页，则自动跳过该页并继续处理同一 PDF 的下一页。

## 使用脚本

执行：

```bash
python3 scripts/merge_scan_file.py <pdf_a> <pdf_b> --output-dir <output_dir>
```

示例：

```bash
python3 scripts/merge_scan_file.py ./PDF-001.pdf ./PDF-002.pdf --output-dir .
```

脚本输出：
- `merge_mode=marker` 或 `merge_mode=fallback-filename-interleave`
- `output=<最终文件路径>`

## 依赖

- 必需：`pypdf`
- 可选（提升扫描件识别率）：`PyMuPDF`、`pytesseract`、`Pillow`，以及系统安装 `tesseract`

安装示例：

```bash
pip install pypdf pymupdf pytesseract pillow
```

若未安装 OCR 依赖，脚本仍可运行，但会更依赖回退规则。
