from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# 可选的进度回调:processor 在长耗时提取(MinerU)中逐条上报人类可读进度串。
# 大多数 processor(纯文本/pypdf 等瞬时完成)忽略它。
ProgressCb = Callable[[str], None]


@dataclass(frozen=True)
class MediaResult:
    text: str
    metadata: dict = field(default_factory=dict)


class MediaProcessor(ABC):
    @abstractmethod
    def supports(self, path: Path) -> bool: ...

    @abstractmethod
    def process(
        self,
        path: Path,
        *,
        progress_cb: ProgressCb | None = None,
        cancel: threading.Event | None = None,
    ) -> MediaResult:
        """提取文本。progress_cb 给定时,长耗时实现(MinerU)应逐条上报进度;
        cancel 给定且被 set 时,长耗时实现应尽快中止(杀子进程并抛错),供调用方
        在客户端断开时停掉提取。瞬时实现忽略二者即可(签名统一便于调用方一律传)。"""
        ...
