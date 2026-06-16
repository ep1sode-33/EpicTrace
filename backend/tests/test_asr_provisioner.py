from pathlib import Path

from epictrace.asr.provisioner import AsrProvisioner


def _make_model_dir(cache_dir: Path, repo: str, *, with_weights: bool = True) -> None:
    """造一个 HF 缓存里的模型仓库目录。with_weights 决定是否含真权重 model.bin
    (False = 模拟残缺/中断下载:有 config/tokenizer 但 model.bin 缺失)。"""
    snap = cache_dir / repo / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}", encoding="utf-8")
    (snap / "tokenizer.json").write_text("{}", encoding="utf-8")
    if with_weights:
        (snap / "model.bin").write_bytes(b"\x00" * 16)


def test_not_ready_when_model_absent(tmp_path: Path):
    prov = AsrProvisioner(cache_dir=tmp_path)
    assert prov.is_ready("large-v3") is False
    assert prov.state == "not_downloaded"


def test_partial_download_without_model_bin_not_ready(tmp_path: Path):
    # 残缺下载:目录在、有 config/tokenizer,但 model.bin 缺(仍是 .incomplete)→ 未就绪。
    _make_model_dir(tmp_path, "models--Systran--faster-whisper-large-v3", with_weights=False)
    prov = AsrProvisioner(cache_dir=tmp_path)
    assert prov.is_ready("large-v3") is False


def test_download_then_ready(tmp_path: Path):
    calls = []

    def fake_runner(model, cache_dir, progress_cb):
        # 假装下载:建出含真权重的模型目录
        _make_model_dir(cache_dir, f"models--Systran--faster-whisper-{model}", with_weights=True)
        progress_cb("done")
        calls.append(model)

    prov = AsrProvisioner(cache_dir=tmp_path, download_runner=fake_runner)
    prov.download_model("large-v3", progress_cb=lambda m: None)
    assert calls == ["large-v3"]
    assert prov.is_ready("large-v3") is True
    assert prov.state == "ready"


def test_model_bin_directory_is_not_ready(tmp_path: Path):
    """FIX 4:snapshots/<h>/model.bin 是个目录(而非真权重文件)→ 未就绪。
    旧实现只看 exists()+st_size>0,目录也能蒙混过关。"""
    snap = (tmp_path / "models--Systran--faster-whisper-large-v3"
            / "snapshots" / "abc123")
    snap.mkdir(parents=True)
    (snap / "model.bin").mkdir()  # model.bin 是目录,不是文件
    prov = AsrProvisioner(cache_dir=tmp_path)
    assert prov.is_ready("large-v3") is False


def test_distil_model_readiness_resolves_repo_dir(tmp_path: Path):
    """FIX G:distil-large-v3 解析到 HF repo Systran/faster-distil-whisper-large-v3,
    缓存目录 models--Systran--faster-distil-whisper-large-v3 须被 is_ready 命中(含权重)。"""
    _make_model_dir(tmp_path, "models--Systran--faster-distil-whisper-large-v3", with_weights=True)
    prov = AsrProvisioner(cache_dir=tmp_path)
    assert prov.is_ready("distil-large-v3") is True
    # 别名只对 distil;普通模型不该被这个 distil 目录误命中。
    assert prov.is_ready("large-v3") is False
