from pathlib import Path

from epictrace.config import AppConfig


def test_config_uses_given_data_dir(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path)
    assert cfg.data_dir == tmp_path
    assert cfg.db_path == tmp_path / "epictrace.db"
    assert cfg.sqlalchemy_url == f"sqlite:///{tmp_path / 'epictrace.db'}"


def test_config_default_data_dir_is_created():
    cfg = AppConfig()
    assert cfg.data_dir.exists()
