from unittest.mock import MagicMock

import pytest

from classroom_mcp import server


def _page(items, key, token=None):
    req = MagicMock()
    resp = {key: items}
    if token:
        resp["nextPageToken"] = token
    req.execute.return_value = resp
    return req


def test_materials_maps_all_known_types():
    mats = [
        {"driveFile": {"driveFile": {"id": "d1", "title": "Notes", "alternateLink": "u1"}}},
        {"link": {"title": "Site", "url": "u2"}},
        {"youtubeVideo": {"title": "Vid", "alternateLink": "u3"}},
        {"form": {"title": "Quiz", "formUrl": "u4"}},
        {"unknown": {}},
    ]
    out = server._materials(mats)
    assert [m["tipo"] for m in out] == ["drive", "enlace", "youtube", "formulario"]
    assert out[0]["id"] == "d1"


def test_materials_handles_none():
    assert server._materials(None) == []


def test_due_none_and_defaults():
    assert server._due({}) is None
    assert server._due({"dueDate": {"year": 2026, "month": 3, "day": 5}}) == "2026-03-05 23:59 UTC"


def test_due_with_time():
    cw = {
        "dueDate": {"year": 2026, "month": 12, "day": 1},
        "dueTime": {"hours": 8, "minutes": 5},
    }
    assert server._due(cw) == "2026-12-01 08:05 UTC"


def test_paginate_follows_tokens_and_respects_limit():
    pages = {None: _page([1, 2], "k", "t1"), "t1": _page([3, 4], "k")}
    assert server._paginate(lambda t: pages[t], "k", 10) == [1, 2, 3, 4]
    pages = {None: _page([1, 2], "k", "t1"), "t1": _page([3, 4], "k")}
    assert server._paginate(lambda t: pages[t], "k", 3) == [1, 2, 3]


def test_listar_cursos(monkeypatch):
    svc = MagicMock()
    svc.courses.return_value.list.return_value.execute.return_value = {
        "courses": [{"id": "1", "name": "Maths", "courseState": "ACTIVE", "alternateLink": "x"}]
    }
    monkeypatch.setattr(server, "_classroom", lambda: svc)
    out = server.listar_cursos()
    assert out == [{"id": "1", "nombre": "Maths", "seccion": None, "estado": "ACTIVE", "url": "x"}]
    kwargs = svc.courses.return_value.list.call_args.kwargs
    assert kwargs["courseStates"] == ["ACTIVE"]


def test_creds_missing_token(monkeypatch, tmp_path):
    monkeypatch.setenv("CLASSROOM_MCP_HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="token.json"):
        server._creds()


def test_home_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLASSROOM_MCP_HOME", str(tmp_path))
    assert server.token_path() == tmp_path / "token.json"
    monkeypatch.delenv("CLASSROOM_DOWNLOAD_DIR", raising=False)
    assert server.download_dir() == tmp_path / "downloads"
