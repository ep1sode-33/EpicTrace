from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from epictrace.config import AppConfig


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._engine = create_engine(
            config.sqlalchemy_url,
            connect_args={"check_same_thread": False},
        )
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)

    @property
    def config(self) -> AppConfig:
        """构造此 Database 时用的 AppConfig(data_dir 等);供 create_app 设 app.state.config,
        保证 settings/get_llm 用同一 data_dir(tmp 测试隔离)。"""
        return self._config

    def create_all(self) -> None:
        # 确保所有 model 已 import 后再建表
        from epictrace import models  # noqa: F401

        Base.metadata.create_all(self._engine)
        # 轻量迁移:create_all 不会给「已存在」的表补新列。旧库的 ingest_records
        # 缺 source_session_id 时,归类会报 'no such column'。检测缺列则 ALTER 补上。
        with self._engine.begin() as conn:
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(ingest_records)")}
            if "source_session_id" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE ingest_records ADD COLUMN source_session_id INTEGER"
                )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
