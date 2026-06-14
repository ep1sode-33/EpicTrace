from __future__ import annotations


class ExtractionEngineNotReady(Exception):
    """富文档(pdf/docx/pptx)提取引擎(MinerU)尚未 provision/就绪。调用方应提示用户先安装高质量提取引擎。"""


class ExtractionFailed(Exception):
    """MinerU 子进程失败/超时/缺输出/空文本。无回退——调用方按既有失败路径呈现。"""
