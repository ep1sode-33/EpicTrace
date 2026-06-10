from __future__ import annotations

from fastapi import Request

from epictrace.db import Database


def get_db(request: Request) -> Database:
    return request.app.state.db
