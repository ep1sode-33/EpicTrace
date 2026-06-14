import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("EPICTRACE_RUN_SLOW") != "1",
    reason="real-mineru extraction test; set EPICTRACE_RUN_SLOW=1 to run",
)


def test_real_mineru_extracts_a_pdf(tmp_path):
    """Sketch: against a provisioned .MinerU-venv, run the real mineru on a
    small generated PDF and assert non-empty markdown + a content_list.
    Skips if MinerU is not provisioned (no model download forced here)."""
    from reportlab.pdfgen import canvas

    from epictrace.config import AppConfig
    from epictrace.media.mineru import MinerUMediaProcessor
    from epictrace.media.mineru_provisioner import MinerUProvisioner

    config = AppConfig()
    prov = MinerUProvisioner(config.mineru_venv_dir)
    if not prov.is_ready():
        pytest.skip("MinerU not provisioned; install via settings first")

    pdf = tmp_path / "sample.pdf"
    c = canvas.Canvas(str(pdf))
    c.drawString(72, 720, "Hello high quality extraction world")
    c.save()

    proc = MinerUMediaProcessor(
        prov, model_source=config.model_source, timeout=config.extraction_timeout,
        effort=config.extraction_effort,
    )
    result = proc.process(pdf)
    assert result.text.strip()
    assert result.metadata["backend"] == "mineru-hybrid"
    assert isinstance(result.metadata["content_list"], list)
