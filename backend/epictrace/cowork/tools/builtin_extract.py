"""文档提取工具(需求 3 工具清单):extract_pdf / extract_docx / extract_pptx。

复用 `epictrace.media` 的 MediaProcessor 管线(get_processor 按用户设置选引擎:
轻量 pypdf 系或 MinerU);路径守卫与 builtin_fs 一致——只允许提取项目文件夹内的文件。
提取结果按字符上限截断回传,超长文档引导 LLM 分段处理(见 pdf-reading skill)。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from epictrace.db import Database
from epictrace.media import get_processor
from epictrace.cowork.tools.builtin_fs import _project, _resolve_in
from epictrace.cowork.tools.registry import ToolDef

_EXTRACT_LIMIT = 20_000
# 转写子进程超时:模型加载 + 长音频,给足 30 分钟
_TRANSCRIBE_TIMEOUT = 1800

# 工具名 → (扩展名, 说明)
_KINDS = {
    "extract_pdf": (".pdf", "PDF"),
    "extract_docx": (".docx", "Word"),
    "extract_pptx": (".pptx", "PowerPoint"),
}


def build_extract_tools(db: Database) -> list[ToolDef]:
    def _extract(project_id: int, path: str, ext: str, kind: str) -> str:
        proj = _project(db, project_id)
        if proj is None:
            return f"Error: project {project_id} not found"
        target = _resolve_in(Path(proj.folder_path), path)
        if target is None or not target.is_file():
            return f"Error: file not found in project: {path}"
        if target.suffix.lower() != ext:
            return f"Error: {path} 不是 {kind} 文件(扩展名应为 {ext})"
        processor = get_processor(target, db.config)
        if processor is None:
            return f"Error: 没有可用的 {kind} 提取器(检查提取引擎设置)"
        try:
            result = processor.process(target)
        except Exception as e:  # noqa: BLE001 — 提取失败是观察,回传给 LLM
            return f"Error: 提取失败({type(e).__name__}: {e})"
        text = result.text or ""
        if not text.strip():
            return (
                f"{path}:未提取到文本。可能是扫描件(无文本层)或损坏文件;"
                "不要重复同样的提取,向用户说明情况或换 MinerU 引擎重试。"
            )
        if len(text) > _EXTRACT_LIMIT:
            return (f"{path}(共 {len(text)} 字符,截取前 {_EXTRACT_LIMIT}):\n"
                    + text[:_EXTRACT_LIMIT]
                    + f"\n…(已截断。可分段处理,或用 run_python 在沙箱里自行解析全量)")
        return f"{path} 提取结果:\n{text}"

    def _make(name: str, ext: str, kind: str):
        def handler(project_id: int, path: str) -> str:
            return _extract(project_id, path, ext, kind)

        return ToolDef(
            name=name,
            description=(
                f"提取项目内 {kind} 文件({ext})的文本内容(含表格结构,尽力保留)。"
                "超长文档会截断,需分段处理。扫描件可能提不出文本。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "path": {"type": "string", "description": "项目内相对路径"},
                },
                "required": ["project_id", "path"],
            },
            handler=handler,
            permission="allow",
        )

    return [_make(name, ext, kind) for name, (ext, kind) in _KINDS.items()]


_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".aiff", ".aif"}


def _fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def build_transcribe_tool(db: Database, *, is_asr_ready: Callable[[], bool]) -> ToolDef:
    """transcribe_audio(需求 3):项目内音频 → mlx-whisper 整文件转写(隔离子进程)。

    is_asr_ready 是 ASR 模型就绪门(请求侧注入 deps.get_asr_provisioner);未就绪
    返回引导文本而不是触发 3GB 下载。
    """

    def transcribe_audio(project_id: int, path: str) -> str:
        proj = _project(db, project_id)
        if proj is None:
            return f"Error: project {project_id} not found"
        target = _resolve_in(Path(proj.folder_path), path)
        if target is None or not target.is_file():
            return f"Error: file not found in project: {path}"
        if target.suffix.lower() not in _AUDIO_EXTS:
            return f"Error: {path} 不是支持的音频格式({', '.join(sorted(_AUDIO_EXTS))})"
        if not is_asr_ready():
            return ("Error: ASR 模型未就绪。请先在「设置 → ASR」下载模型后再试"
                    "(模型约 3GB,只下载一次)。")
        try:
            # GRPC_*:主进程若已激活 Milvus(gRPC),fork 子进程会往 stdout/stderr 写 poll 噪声,
            # 污染 JSON stdout(见 sandbox.py 同处理 + macos-embedding-milvus-fork-order)
            env = {**os.environ,
                   "GRPC_ENABLE_FORK_SUPPORT": "0", "GRPC_VERBOSITY": "ERROR"}
            proc = subprocess.run(
                [sys.executable, "-m", "epictrace.asr.transcribe_file", str(target)],
                capture_output=True, text=True, timeout=_TRANSCRIBE_TIMEOUT, env=env,
            )
        except subprocess.TimeoutExpired:
            return f"Error: 转写超时(>{_TRANSCRIBE_TIMEOUT // 60} 分钟)。文件可能过长,建议先切分。"
        if proc.returncode != 0:
            return f"Error: 转写失败({(proc.stderr or '').strip()[-300:] or 'unknown'})"
        try:
            segments = json.loads(proc.stdout)["segments"]
        except (json.JSONDecodeError, KeyError) as e:
            return f"Error: 转写结果解析失败({e})"
        if not segments:
            return f"{path}:未识别到语音内容(可能是纯静音或质量过低)。"
        lines = [f"[{_fmt_ts(s['start'])}] {s['text']}" for s in segments]
        text = "\n".join(lines)
        if len(text) > _EXTRACT_LIMIT:
            text = (f"(共 {len(segments)} 段,截取前 {_EXTRACT_LIMIT} 字符)\n"
                    + text[:_EXTRACT_LIMIT] + "\n…(已截断)")
        return f"{path} 转写结果:\n{text}"

    return ToolDef(
        name="transcribe_audio",
        description=(
            "把项目内的音频文件转写成带时间戳的文字(mlx-whisper 整文件转写,"
            "后台子进程执行,长音频需要几分钟)。返回 [mm:ss] 开头的分段文本。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "path": {"type": "string", "description": "项目内相对路径(wav/mp3/m4a 等)"},
            },
            "required": ["project_id", "path"],
        },
        handler=transcribe_audio,
        permission="ask",  # 拉起模型子进程,资源开销大,先确认
    )
