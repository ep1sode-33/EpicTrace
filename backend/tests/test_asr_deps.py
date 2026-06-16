import importlib.util
from pathlib import Path

import pytest

from epictrace.config import AppConfig


def _missing(mod: str) -> bool:
    return importlib.util.find_spec(mod) is None


def test_asr_model_dir_under_data_dir(tmp_path: Path):
    """ASR 模型缓存目录:data_dir 下的固定子目录(faster-whisper download_root)。"""
    cfg = AppConfig(data_dir=tmp_path)
    assert cfg.asr_model_dir == tmp_path / ".asr-models"


# 重依赖(faster-whisper/CTranslate2、PortAudio)未必装在 CI/dev 环境;装了才跑这组
# import 烟测,没装则跳过(绝不让测试套件硬依赖这些重件)。
@pytest.mark.skipif(_missing("faster_whisper"),
                    reason="faster-whisper not installed (heavy CTranslate2 dep; opt-in)")
def test_faster_whisper_importable():
    import faster_whisper  # noqa: F401


@pytest.mark.skipif(_missing("sounddevice"),
                    reason="sounddevice not installed (needs PortAudio; opt-in)")
def test_sounddevice_importable():
    import sounddevice  # noqa: F401


@pytest.mark.skipif(_missing("soundfile"),
                    reason="soundfile not installed (opt-in)")
def test_soundfile_importable():
    import soundfile  # noqa: F401
