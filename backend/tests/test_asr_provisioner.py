from pathlib import Path

from epictrace.asr.provisioner import AsrProvisioner


def test_not_ready_when_model_absent(tmp_path: Path):
    prov = AsrProvisioner(cache_dir=tmp_path)
    assert prov.is_ready("large-v3") is False
    assert prov.state == "not_downloaded"


def test_download_then_ready(tmp_path: Path):
    calls = []
    def fake_runner(model, cache_dir, progress_cb):
        # 假装下载:在缓存里建出 faster-whisper 期望的目录
        (cache_dir / f"models--Systran--faster-whisper-{model}").mkdir(parents=True)
        progress_cb("done")
        calls.append(model)
    prov = AsrProvisioner(cache_dir=tmp_path, download_runner=fake_runner)
    prov.download_model("large-v3", progress_cb=lambda m: None)
    assert calls == ["large-v3"]
    assert prov.is_ready("large-v3") is True
    assert prov.state == "ready"
