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


def test_mlx_oneshot_provisioner_ready(monkeypatch):
    """架构转单遍 mlx:MlxOneshotProvisioner.is_ready/state 据 mlx_model_ready(忽略 faster-whisper model 名)。"""
    import epictrace.asr.provisioner as prov
    monkeypatch.setattr(prov, "mlx_model_ready", lambda repo=prov.MLX_ONESHOT_REPO: True)
    p = prov.MlxOneshotProvisioner()
    assert p.is_ready("large-v3") is True and p.state == "ready"


def test_mlx_oneshot_provisioner_not_ready(monkeypatch):
    import epictrace.asr.provisioner as prov
    monkeypatch.setattr(prov, "mlx_model_ready", lambda repo=prov.MLX_ONESHOT_REPO: False)
    p = prov.MlxOneshotProvisioner()
    assert p.is_ready() is False and p.state == "not_downloaded"


def test_mlx_oneshot_provisioner_download_calls_snapshot(monkeypatch):
    """未就绪 → download_model 调 snapshot_download 拉 mlx 完整 v3 repo。"""
    import sys
    import types

    import epictrace.asr.provisioner as prov
    monkeypatch.setattr(prov, "mlx_model_ready", lambda repo=prov.MLX_ONESHOT_REPO: False)
    called = {}
    fake = types.ModuleType("huggingface_hub")
    fake.snapshot_download = lambda repo: called.setdefault("repo", repo)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)
    p = prov.MlxOneshotProvisioner()
    p.download_model("large-v3")
    assert called["repo"] == prov.MLX_ONESHOT_REPO


def test_snapshot_has_weights_requires_real_weights(tmp_path):
    """就绪检测须命中真权重(config.json + weights.npz/*.safetensors);只有空/半快照目录不算就绪
    (修复:旧实现只看 snapshots 非空 → 残缺下载误判就绪 → 一次性转写崩)。测纯 FS 逻辑,与平台无关。"""
    from epictrace.asr.provisioner import _snapshot_has_weights
    snaps = tmp_path / "snapshots"
    assert _snapshot_has_weights(snaps) is False     # 目录不存在
    snap = snaps / "abc123"
    snap.mkdir(parents=True)
    assert _snapshot_has_weights(snaps) is False     # 空快照
    (snap / "config.json").write_text("{}")
    assert _snapshot_has_weights(snaps) is False     # 只有 config、无权重
    (snap / "weights.npz").write_text("x")
    assert _snapshot_has_weights(snaps) is True       # config + weights.npz → 就绪
    # *.safetensors 布局也认。
    snap2 = snaps / "def456"
    snap2.mkdir()
    (snap2 / "config.json").write_text("{}")
    (snap2 / "model.safetensors").write_text("x")
    assert _snapshot_has_weights(snaps) is True


def test_snapshot_has_weights_rejects_fake_weight_entries(tmp_path):
    """非真权重的伪就绪都挡掉:0 字节占位、目录冒充权重、断链(中断下载在 snapshots 留的坏软链)。
    并验证**跟随符号链接**——HF 缓存里 snapshots/ 的项都是指向 blobs/ 的软链,指到真文件才算就绪。"""
    from epictrace.asr.provisioner import _snapshot_has_weights
    snaps = tmp_path / "snapshots"

    # ① 空(0 字节)config + 空 weights → 不算就绪。
    a = snaps / "aaa"
    a.mkdir(parents=True)
    (a / "config.json").write_text("")
    (a / "weights.npz").write_text("")
    assert _snapshot_has_weights(snaps) is False
    # config 补成非空,但 weights 仍 0 字节 → 仍不就绪。
    (a / "config.json").write_text("{}")
    assert _snapshot_has_weights(snaps) is False

    # ② weights.npz 是个目录(而非文件)→ 不算就绪。
    b = snaps / "bbb"
    b.mkdir()
    (b / "config.json").write_text("{}")
    (b / "weights.npz").mkdir()
    assert _snapshot_has_weights(snaps) is False

    # ③ *.safetensors 是断链(中断下载:snapshots 软链指向不存在的 blob)→ 不算就绪。
    c = snaps / "ccc"
    c.mkdir()
    (c / "config.json").write_text("{}")
    (c / "model.safetensors").symlink_to(tmp_path / "nonexistent-blob")
    assert _snapshot_has_weights(snaps) is False

    # ④ 软链指向真·非空 blob → 就绪(模拟 HF 缓存正常布局:snapshots→blobs 软链)。
    blob = tmp_path / "blobs" / "deadbeef"
    blob.parent.mkdir(parents=True)
    blob.write_text("real-weights-bytes")
    (c / "model.safetensors").unlink()
    (c / "model.safetensors").symlink_to(blob)
    assert _snapshot_has_weights(snaps) is True
