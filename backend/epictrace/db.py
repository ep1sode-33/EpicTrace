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
