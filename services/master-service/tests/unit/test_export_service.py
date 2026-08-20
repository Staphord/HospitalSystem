"""Unit tests for export_service.py in master-service.
"""
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from datetime import datetime, date, timezone
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import create_engine, Table, Column, Integer, String, MetaData

from app.services.export_service import _serialize_value, _table_to_dicts, export_tenant_data

def test_serialize_value():
    now_dt = datetime.now(timezone.utc)
    now_d = date.today()
    u = uuid4()
    dec = Decimal("10.50")
    b = b"bytes"

    assert _serialize_value(now_dt) == now_dt.isoformat()
    assert _serialize_value(now_d) == now_d.isoformat()
    assert _serialize_value(u) == str(u)
    assert _serialize_value(dec) == 10.50
    assert _serialize_value(b) == "bytes"
    assert _serialize_value({"a": 1}) == {"a": 1}
    assert _serialize_value("string") == "string"

def test_table_to_dicts():
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    mock_row = MagicMock()
    mock_row.id = 1
    mock_row.name = "Test"
    mock_conn.execute.return_value.fetchall.return_value = [mock_row]

    with patch("app.services.export_service.inspect") as mock_inspect:
        mock_inspect.return_value.get_columns.return_value = [{"name": "id"}, {"name": "name"}]
        res = _table_to_dicts(mock_engine, "users")
        assert len(res) == 1
        assert res[0]["id"] == 1
        assert res[0]["name"] == "Test"

def test_table_to_dicts_row_fallback():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    test_table = Table("test_table", metadata, Column("id", Integer, primary_key=True), Column("name", String))
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(test_table.insert(), [{"id": 1, "name": "Test"}])

    res = _table_to_dicts(engine, "test_table")
    assert len(res) == 1
    assert res[0]["name"] == "Test"

def test_table_to_dicts_mapping_fallback():
    mock_engine = MagicMock()
    mock_row = MagicMock()
    del mock_row.col1
    mock_row._mapping = {"col1": "val1"}

    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchall.return_value = [mock_row]
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    with patch("app.services.export_service.inspect") as mock_inspect:
        mock_inspect.return_value.get_columns.return_value = [{"name": "col1"}]
        res = _table_to_dicts(mock_engine, "dummy_table")
        assert len(res) == 1

@pytest.mark.asyncio
async def test_export_tenant_data_no_dsn():
    mock_db = MagicMock()
    with patch("app.services.tenant_service.get_tenant_db_dsn", AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="Could not resolve database URL"):
            await export_tenant_data(mock_db, "invalid_t")

@pytest.mark.asyncio
async def test_export_tenant_data_success():
    mock_db = MagicMock()
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    with patch("app.services.tenant_service.get_tenant_db_dsn", AsyncMock(return_value="sqlite:///:memory:")):
        with patch("app.services.export_service.create_engine", return_value=mock_engine):
            with patch("app.services.export_service.inspect") as mock_inspect:
                mock_inspect.return_value.get_table_names.return_value = ["patients"]
                with patch("app.services.export_service._table_to_dicts", return_value=[{"id": 1}]):
                    res = await export_tenant_data(mock_db, "t1")
                    assert "patients" in res
                    assert res["patients"] == [{"id": 1}]

@pytest.mark.asyncio
async def test_export_tenant_data_table_error():
    with patch("app.services.tenant_service.get_tenant_db_dsn", AsyncMock(return_value="sqlite:///:memory:")):
        with patch("app.services.export_service.inspect") as mock_inspect:
            mock_inspect.return_value.get_table_names.return_value = ["bad_table"]
            with patch("app.services.export_service._table_to_dicts", side_effect=Exception("Table corrupt")):
                db = MagicMock()
                res = await export_tenant_data(db, "t_bad")
                assert res["bad_table"] == []
