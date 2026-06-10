from pathlib import Path

from sqlalchemy import text

from epictrace.config import AppConfig
from epictrace.db import Database


def test_database_session_executes(tmp_path: Path):
    db = Database(AppConfig(data_dir=tmp_path))
    db.create_all()
    with db.session() as s:
        assert s.execute(text("select 1")).scalar_one() == 1
