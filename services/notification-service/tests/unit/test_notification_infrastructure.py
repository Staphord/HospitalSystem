"""Infrastructure, events, security, middleware, broadcaster, and messaging unit tests for notification-service.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from fastapi.testclient import TestClient
from jose import jwt
import pytest
from fastapi import HTTPException

from app.core import config as cfg_mod
from app.core import middleware as mid_mod
from app.core import tenant_auth as ta_mod
from app.core import security as sec_mod
from app.core import database as db_mod
from app.events import subscriber as sub_mod
from app.services import broadcaster as bcast_mod
from app.services import tenant_service as ts_mod
from app.api.v1.schemas import NotificationItemResponse


# ---------------------------------------------------------------------------
# Broadcaster Unit Tests
# ---------------------------------------------------------------------------

class TestNotificationBroadcaster:
    @pytest.mark.asyncio
    async def test_broadcaster_subscribe_unsubscribe_and_broadcast(self):
        bc = bcast_mod.NotificationBroadcaster()
        mock_redis = AsyncMock()
        bc._redis = mock_redis

        with patch.object(bc, "_listen", AsyncMock()):
            q = bc.subscribe("t1")
            assert q in bc._local_queues["t1"]

            mock_item = {"id": str(uuid4()), "message": "Test notif"}
            await bc.broadcast("t1", mock_item)
            mock_redis.publish.assert_awaited_once()

            bc.unsubscribe("t1", q)
            assert "t1" not in bc._local_queues

    @pytest.mark.asyncio
    async def test_broadcaster_no_redis_skips_broadcast(self):
        bc = bcast_mod.NotificationBroadcaster()
        bc._redis = None
        await bc.broadcast("t1", {"msg": "hello"})

    @pytest.mark.asyncio
    async def test_broadcaster_all_branch_edge_cases(self):
        import asyncio
        bc = bcast_mod.NotificationBroadcaster()

        await bc.disconnect()

        q1 = bc.subscribe("t_dup")
        q2 = bc.subscribe("t_dup")
        assert "t_dup" in bc._local_queues
        assert len(bc._local_queues["t_dup"]) == 2

        bc.unsubscribe("t_dup", q1)
        bc.unsubscribe("t_dup", q2)
        assert "t_dup" not in bc._local_queues

        bc.unsubscribe("t_nonexistent", q1)

        await bc.broadcast("t_nobroadcast", {"test": "data"})

        await bc._listen("t_noredis")

        class FakePubSubFull:
            async def subscribe(self, channel): pass
            async def unsubscribe(self, channel): pass
            async def aclose(self): pass
            async def listen(self):
                yield {"type": "message", "data": "item1"}

        mock_redis = MagicMock()
        mock_redis.pubsub.return_value = FakePubSubFull()
        bc._redis = mock_redis

        full_q = asyncio.Queue(maxsize=1)
        full_q.put_nowait("existing")
        bc._local_queues["t_full"] = {full_q}

        await bc._listen("t_full")

    @pytest.mark.asyncio
    async def test_broadcaster_listen_loop(self):
        import json, asyncio
        bc = bcast_mod.NotificationBroadcaster()
        
        class FakePubSub:
            async def subscribe(self, channel): pass
            async def unsubscribe(self, channel): pass
            async def aclose(self): pass
            async def listen(self):
                yield {"type": "message", "data": json.dumps({"hello": "world"})}

        mock_redis = MagicMock()
        mock_redis.pubsub.return_value = FakePubSub()
        bc._redis = mock_redis

        q = asyncio.Queue()
        bc._local_queues["t1"] = {q}

        await bc._listen("t1")
        assert not q.empty()


# ---------------------------------------------------------------------------
# Subscriber Handlers Unit Tests
# ---------------------------------------------------------------------------

class TestNotificationSubscriberHandlers:
    @pytest.mark.asyncio
    async def test_handle_lab_critical_value(self):
        mock_db = AsyncMock()
        mock_item = NotificationItemResponse(
            notification_id=uuid4(),
            tenant_id="t1",
            recipient_id=None,
            recipient_role="doctor",
            title="Critical Laboratory Result",
            message="msg",
            category="clinical",
            priority="emergency",
            status="unread",
            action_url=None,
            metadata_payload={},
            read_at=None,
            created_at=datetime.now(timezone.utc),
        )
        async def fake_session(t_id):
            yield mock_db

        with patch("app.events.subscriber.get_tenant_session", fake_session):
            with patch("app.events.subscriber.notification_service.create_notification", new=AsyncMock(return_value=mock_item)):
                with patch("app.events.subscriber.broadcaster.broadcast", AsyncMock()):
                    await sub_mod.handle_lab_critical_value({"patient_name": "John", "test_name": "Blood Test"}, "t1")

    @pytest.mark.asyncio
    async def test_handle_radiology_report_ready(self):
        mock_db = AsyncMock()
        mock_item = NotificationItemResponse(
            notification_id=uuid4(),
            tenant_id="t1",
            recipient_id=None,
            recipient_role="doctor",
            title="Imaging Report Ready",
            message="msg",
            category="clinical",
            priority="normal",
            status="unread",
            action_url=None,
            metadata_payload={},
            read_at=None,
            created_at=datetime.now(timezone.utc),
        )
        async def fake_session(t_id):
            yield mock_db

        with patch("app.events.subscriber.get_tenant_session", fake_session):
            with patch("app.events.subscriber.notification_service.create_notification", new=AsyncMock(return_value=mock_item)):
                with patch("app.events.subscriber.broadcaster.broadcast", AsyncMock()):
                    await sub_mod.handle_radiology_report_ready({"patient_name": "John", "study_type": "X-Ray"}, "t1")

    @pytest.mark.asyncio
    async def test_handle_patient_admitted(self):
        mock_db = AsyncMock()
        mock_item = NotificationItemResponse(
            notification_id=uuid4(), tenant_id="t1", recipient_id=None, recipient_role="ward_nurse",
            title="New Patient Admission", message="msg", category="clinical", priority="normal",
            status="unread", action_url=None, metadata_payload={}, read_at=None, created_at=datetime.now(timezone.utc),
        )
        async def fake_session(t_id): yield mock_db

        with patch("app.events.subscriber.get_tenant_session", fake_session):
            with patch("app.events.subscriber.notification_service.create_notification", new=AsyncMock(return_value=mock_item)):
                with patch("app.events.subscriber.broadcaster.broadcast", AsyncMock()):
                    await sub_mod.handle_patient_admitted({"patient_name": "Jane", "ward_name": "ICU"}, "t1")

    @pytest.mark.asyncio
    async def test_handle_prescription_issued(self):
        mock_db = AsyncMock()
        mock_item = NotificationItemResponse(
            notification_id=uuid4(), tenant_id="t1", recipient_id=None, recipient_role="pharmacist",
            title="New Prescription Issued", message="msg", category="pharmacy", priority="normal",
            status="unread", action_url=None, metadata_payload={}, read_at=None, created_at=datetime.now(timezone.utc),
        )
        async def fake_session(t_id): yield mock_db

        with patch("app.events.subscriber.get_tenant_session", fake_session):
            with patch("app.events.subscriber.notification_service.create_notification", new=AsyncMock(return_value=mock_item)):
                with patch("app.events.subscriber.broadcaster.broadcast", AsyncMock()):
                    await sub_mod.handle_prescription_issued({"patient_name": "Jane"}, "t1")

    @pytest.mark.asyncio
    async def test_handle_payment_received(self):
        mock_db = AsyncMock()
        mock_item = NotificationItemResponse(
            notification_id=uuid4(), tenant_id="t1", recipient_id=None, recipient_role="cashier",
            title="Payment Processed", message="msg", category="billing", priority="normal",
            status="unread", action_url=None, metadata_payload={}, read_at=None, created_at=datetime.now(timezone.utc),
        )
        async def fake_session(t_id): yield mock_db

        with patch("app.events.subscriber.get_tenant_session", fake_session):
            with patch("app.events.subscriber.notification_service.create_notification", new=AsyncMock(return_value=mock_item)):
                with patch("app.events.subscriber.broadcaster.broadcast", AsyncMock()):
                    await sub_mod.handle_payment_received({"amount": 5000, "receipt_number": "R123"}, "t1")

    @pytest.mark.asyncio
    async def test_dispatch_all_routing_keys(self):
        mock_db = AsyncMock()
        mock_item = NotificationItemResponse(
            notification_id=uuid4(), tenant_id="t1", recipient_id=None, recipient_role="doctor",
            title="Title", message="msg", category="clinical", priority="normal",
            status="unread", action_url=None, metadata_payload={}, read_at=None, created_at=datetime.now(timezone.utc),
        )
        async def fake_session(t_id): yield mock_db

        routing_keys = [
            "lab.critical_value", "lab.result_ready", "radiology.report_ready", "stock.low",
            "drug.dispensed", "patient.admitted", "patient.discharged", "patient.registered",
            "prescription.issued", "investigation.requested", "payment.received", "bill.created",
            "visit.created", "patient.checked_in", "triage.completed", "announcement.created",
            "tenant.created", "tenant.activated", "tenant.suspended", "tenant.reactivated",
            "user.created", "user.deactivated", "subscription.invoice_generated",
            "subscription.invoice_overdue", "subscription_request.processed", "subscription_request.created",
            "unknown.key"
        ]

        mock_master_db = MagicMock()
        mock_master_db.execute.return_value.fetchall.return_value = [("t1",)]

        with patch("app.events.subscriber.get_tenant_session", fake_session):
            with patch("app.db.master.get_master_db", return_value=mock_master_db):
                with patch("app.events.subscriber.notification_service.create_notification", new=AsyncMock(return_value=mock_item)):
                    with patch("app.events.subscriber.broadcaster.broadcast", AsyncMock()):
                        for rk in routing_keys:
                            await sub_mod._dispatch(rk, {"tenant_id": "t1", "hospital_name": "Test Hosp", "urgency": "STAT", "request_type": "lab"})

    @pytest.mark.asyncio
    async def test_subscriber_lab_and_investigation_edge_cases(self):
        from app.events import subscriber as sub_mod

        mock_db = AsyncMock()
        async def fake_tenant_session(t_id): yield mock_db

        with patch("app.events.subscriber.get_tenant_session", fake_tenant_session):
            with patch("app.events.subscriber.broadcaster.broadcast", AsyncMock()):
                # Critical lab result with test_name and requested_by
                payload_lab = {"test_name": "Blood Culture", "is_critical": True, "requested_by": "Dr. Smith"}
                await sub_mod.handle_lab_result_ready(payload_lab, "t1")

                # Urgent investigation request for radiology
                payload_inv = {
                    "test_name": "Chest CT",
                    "urgency": "URGENT",
                    "request_type": "radiology",
                    "requested_by": "Dr. Jones",
                    "consultation_id": "c-123456789",
                }
                await sub_mod.handle_investigation_requested(payload_inv, "t1")

                # Routine investigation request
                payload_routine = {"urgency": "ROUTINE", "request_type": "lab"}
                await sub_mod.handle_investigation_requested(payload_routine, "t1")

        # Announcement master DB error handling
        mock_master_db = MagicMock()
        mock_master_db.execute.side_effect = Exception("DB Error")
        with patch("app.db.master.get_master_db", return_value=mock_master_db):
            await sub_mod.handle_announcement_created({"title": "T1", "body": "B1"})


class TestNotificationTenantService:
    def test_cipher_encrypt_decrypt(self):
        enc = ts_mod.encrypt_dsn("postgresql://localhost/db")
        assert ts_mod.decrypt_dsn(enc) == "postgresql://localhost/db"

    @pytest.mark.asyncio
    async def test_tenant_suspension_cache(self):
        mock_redis = AsyncMock()
        mock_redis.get.return_value = "1"
        with patch.object(ts_mod, "_get_redis", AsyncMock(return_value=mock_redis)):
            assert await ts_mod.is_tenant_suspended("t1") is True
            await ts_mod.cache_tenant_suspension("t1")
            await ts_mod.remove_tenant_suspension_cache("t1")

    @pytest.mark.asyncio
    async def test_check_tenant_subscription_variants(self):
        mock_db = MagicMock()
        mock_db.execute.return_value.one_or_none.return_value = None
        assert await ts_mod.check_tenant_subscription(mock_db, "t1") == "not_found"

        mock_db.execute.return_value.one_or_none.return_value = ("suspended", None)
        assert await ts_mod.check_tenant_subscription(mock_db, "t1") == "suspended"

        past = datetime.now(timezone.utc) - timedelta(days=1)
        mock_db.execute.return_value.one_or_none.return_value = ("active", past)
        assert await ts_mod.check_tenant_subscription(mock_db, "t1") == "expired"

        future = datetime.now(timezone.utc) + timedelta(days=10)
        mock_db.execute.return_value.one_or_none.return_value = ("active", future)
        assert await ts_mod.check_tenant_subscription(mock_db, "t1") == "active"

    @pytest.mark.asyncio
    async def test_check_and_update_tenant_status_suspended(self):
        mock_db = MagicMock()
        mock_db.execute.return_value.one_or_none.return_value = ("suspended", None, 1)
        with patch.object(ts_mod, "cache_tenant_suspension", AsyncMock()):
            res = await ts_mod.check_and_update_tenant_status(mock_db, "t1")
            assert res == "suspended"

    @pytest.mark.asyncio
    async def test_check_and_update_tenant_status_overdue_30_days(self):
        mock_db = MagicMock()
        past = datetime.now(timezone.utc) - timedelta(days=35)
        mock_db.execute.return_value.one_or_none.return_value = ("active", past, 1)
        with patch.object(ts_mod, "cache_tenant_suspension", AsyncMock()):
            with patch.object(ts_mod, "_revoke_keycloak_sessions", AsyncMock()):
                res = await ts_mod.check_and_update_tenant_status(mock_db, "t1")
                assert res == "suspended"

class TestNotificationExceptionsAndMessaging:
    def test_custom_exceptions(self):
        from app import exceptions as exc_mod
        assert exc_mod.UnauthorizedError().status_code == 401
        assert exc_mod.ForbiddenError().status_code == 403
        assert exc_mod.NotFoundError().status_code == 404
        assert exc_mod.ConflictError().status_code == 409
        assert exc_mod.BadRequestError().status_code == 400
        assert exc_mod.RateLimitError().status_code == 429
        assert exc_mod.TenantNotFoundError().status_code == 404
        assert exc_mod.TokenExpiredError().status_code == 401
        assert exc_mod.MFARequiredError().status_code == 401
        assert exc_mod.TenantSuspendedError().status_code == 403
        assert exc_mod.ReadOnlyScopeError().status_code == 403

    @pytest.mark.asyncio
    async def test_messaging_publisher(self):
        from app.messaging import publisher as msg_pub_mod
        from app.events import publisher as ev_pub_mod

        mock_chan = AsyncMock()
        mock_exch = AsyncMock()
        with patch("app.messaging.publisher.get_channel", AsyncMock(return_value=mock_chan)):
            with patch("app.messaging.publisher.declare_exchange", AsyncMock(return_value=mock_exch)):
                await msg_pub_mod.publish_event("notification.sent", {"id": "1"})
                await ev_pub_mod.publish_notification_sent("n1", "t1")
                assert mock_exch.publish.call_count >= 1

    @pytest.mark.asyncio
    async def test_messaging_connection_and_subscriber(self):
        from app.messaging import connection as conn_mod
        from app.messaging import subscriber as msg_sub_mod
        from contextlib import asynccontextmanager

        mock_conn = AsyncMock()
        mock_conn.is_closed = False
        mock_chan = AsyncMock()
        mock_conn.channel.return_value = mock_chan
        mock_exch = AsyncMock()
        mock_chan.declare_exchange.return_value = mock_exch

        mock_msg = MagicMock()
        mock_msg.body = b'{"key": "val"}'
        mock_msg.routing_key = "test.key"

        @asynccontextmanager
        async def fake_msg_proc():
            yield

        mock_msg.process = fake_msg_proc

        class FakeQueueIter:
            def __aiter__(self):
                return self
            async def __anext__(self):
                if not hasattr(self, "_done"):
                    self._done = True
                    return mock_msg
                raise StopAsyncIteration

        @asynccontextmanager
        async def fake_iterator():
            yield FakeQueueIter()

        mock_queue = AsyncMock()
        mock_queue.iterator = fake_iterator
        mock_chan.declare_queue.return_value = mock_queue

        with patch("aio_pika.connect_robust", AsyncMock(return_value=mock_conn)):
            conn = await conn_mod.get_connection()
            assert conn == mock_conn
            chan = await conn_mod.get_channel()
            assert chan == mock_chan
            exch = await conn_mod.declare_exchange(chan)
            assert exch == mock_exch

            handler_mock = AsyncMock()
            await msg_sub_mod.start_consumer("test_service", ["test.key"], handler_mock)
            handler_mock.assert_awaited_once()

            task = await msg_sub_mod.run_consumer_task("test_service", ["test.key"], handler_mock)
            task.cancel()

            await conn_mod.close_connection()

    @pytest.mark.asyncio
    async def test_tenant_db_factory(self):
        from app.db import tenant as t_db_mod
        from app.dependencies import get_tenant_db, get_current_user
        from app.exceptions import TenantNotFoundError

        mock_factory = MagicMock()
        mock_sess = AsyncMock()
        mock_factory.return_value.__aenter__.return_value = mock_sess

        t_db_mod._async_engine_cache["t_cached"] = mock_factory
        factory = await t_db_mod._get_async_session_factory("t_cached")
        assert factory == mock_factory

        async def fake_session(t_id):
            yield mock_sess

        with patch("app.dependencies.get_tenant_session", fake_session):
            mock_ctx = MagicMock(tenant_id="t1")
            async for s in get_tenant_db(mock_ctx):
                assert s == mock_sess

        mock_user = MagicMock()
        assert await get_current_user(mock_user) == mock_user

    @pytest.mark.asyncio
    async def test_get_async_session_factory_uncached_and_not_found(self):
        from app.db import tenant as t_db_mod
        from app.exceptions import TenantNotFoundError

        mock_db = MagicMock()
        mock_engine = MagicMock()
        mock_sess = AsyncMock()

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_factory = MagicMock(return_value=mock_ctx)

        with patch("app.db.master.get_master_db", return_value=mock_db):
            with patch("app.db.tenant.get_tenant_db_dsn", AsyncMock(return_value="postgresql://u:p@localhost/hosp")):
                with patch("app.db.tenant.create_async_engine", return_value=mock_engine):
                    with patch("app.db.tenant.async_sessionmaker", return_value=mock_factory):
                        fac = await t_db_mod._get_async_session_factory("t_new_123")
                        assert fac == mock_factory

                        async for s in t_db_mod.get_tenant_session("t_new_123"):
                            assert s == mock_sess

        with patch("app.db.master.get_master_db", return_value=mock_db):
            with patch("app.db.tenant.get_tenant_db_dsn", AsyncMock(return_value=None)):
                with pytest.raises(TenantNotFoundError):
                    await t_db_mod._get_async_session_factory("t_missing_999")

class TestNotificationSecurityCore:
    def test_issuer_and_realm_extraction(self):
        assert "hospital-realm" in sec_mod._issuer("hospital-realm")
        assert sec_mod._extract_realm_from_iss("invalid-jwt") is None

    @pytest.mark.asyncio
    async def test_security_extract_realm_and_fetch_jwks(self):
        valid_iss = f"{cfg_mod.settings.keycloak_url}/realms/hospital-realm"
        tok = jwt.encode({"iss": valid_iss}, "secret", algorithm="HS256")
        assert sec_mod._extract_realm_from_iss(tok) == "hospital-realm"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"keys": [{"kid": "k1"}]}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client

        with patch("httpx.AsyncClient", return_value=mock_client):
            jwks = await sec_mod._fetch_jwks("new-realm")
            assert "keys" in jwks

    @pytest.mark.asyncio
    async def test_security_decode_and_introspect(self):
        tok = jwt.encode({"sub": "u1"}, cfg_mod.settings.secret_key, algorithm="HS256")
        res = await sec_mod._decode_token(tok)
        assert res["sub"] == "u1"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"active": True}
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client

        with patch("httpx.AsyncClient", return_value=mock_client):
            await sec_mod._introspect_token("tok-active")

        mock_resp_inact = MagicMock()
        mock_resp_inact.json.return_value = {"active": False}
        mock_client.post.return_value = mock_resp_inact

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(HTTPException) as exc:
                await sec_mod._introspect_token("tok-inact")
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_active_user_full(self):
        payload = {"sub": "u1", "preferred_username": "doc", "realm_access": {"roles": ["doctor"]}}
        tok = jwt.encode(payload, cfg_mod.settings.secret_key, algorithm="HS256")
        creds = MagicMock(scheme="bearer", credentials=tok)
        req = MagicMock()

        with patch("app.core.security.settings.keycloak_introspect", False):
            user = await sec_mod.get_current_active_user(req, credentials=creds)
            assert user.sub == "u1"

        with pytest.raises(HTTPException) as exc:
            await sec_mod.get_current_active_user(req, credentials=None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_security_and_tenant_auth_rs256_decoding(self):
        valid_iss = f"{cfg_mod.settings.keycloak_url}/realms/hospital-realm"
        tok = jwt.encode({"sub": "rs256-user", "iss": valid_iss}, "secret", algorithm="HS256")

        mock_jwks = {"keys": [{"kid": "k-rs256-1"}]}

        with patch("jose.jwt.get_unverified_header", return_value={"alg": "RS256", "kid": "k-rs256-1"}):
            with patch("jose.jwt.decode", return_value={"sub": "rs256-user", "iss": valid_iss}):
                with patch.object(sec_mod, "_fetch_jwks", AsyncMock(return_value=mock_jwks)):
                    res_sec = await sec_mod._decode_token(tok)
                    assert res_sec["sub"] == "rs256-user"

                with patch.object(ta_mod, "_fetch_jwks", AsyncMock(return_value=mock_jwks)):
                    res_ta = await ta_mod._decode_token(tok)
                    assert res_ta["sub"] == "rs256-user"

    @pytest.mark.asyncio
    async def test_security_and_tenant_auth_all_remaining_error_branches(self):
        with pytest.raises(HTTPException) as exc1:
            await sec_mod._decode_token("invalid-raw-jwt-string")
        assert exc1.value.status_code == 401

        with pytest.raises(HTTPException) as exc2:
            await ta_mod._decode_token("invalid-raw-jwt-string")
        assert exc2.value.status_code == 401

        tok_hs = jwt.encode({"sub": "u1", "exp": datetime.now(timezone.utc) - timedelta(hours=1)}, cfg_mod.settings.secret_key, algorithm="HS256")
        with pytest.raises(HTTPException) as exc3:
            await sec_mod._decode_token(tok_hs)
        assert exc3.value.status_code == 401

        with pytest.raises(HTTPException) as exc4:
            await ta_mod._decode_token(tok_hs)
        assert exc4.value.status_code == 401

        tok_bad_sig = jwt.encode({"sub": "u1"}, "wrong-key-secret", algorithm="HS256")
        with pytest.raises(HTTPException) as exc5:
            await sec_mod._decode_token(tok_bad_sig)
        assert exc5.value.status_code == 401

        with pytest.raises(HTTPException) as exc6:
            await ta_mod._decode_token(tok_bad_sig)
        assert exc6.value.status_code == 401

        sa_payload = {"type": "superadmin", "super_admin_id": "sa-99", "username": "admin_qp", "role": "super_admin"}
        tok_qp = jwt.encode(sa_payload, cfg_mod.settings.secret_key, algorithm="HS256")
        req_qp = MagicMock(query_params={"token": tok_qp})
        ctx_qp = await ta_mod.get_current_tenant(req_qp, credentials=None)
        assert ctx_qp.is_super_admin is True
        assert ctx_qp.user_sub == "sa-99"

    @pytest.mark.asyncio
    async def test_security_and_tenant_auth_rs256_kid_exceptions(self):
        tok_rs = jwt.encode({"sub": "u-rs-exp"}, "secret", algorithm="HS256")
        mock_jwks = {"keys": [{"kid": "k-rs-exp"}]}

        with patch("jose.jwt.get_unverified_header", return_value={"alg": "RS256", "kid": "k-rs-exp"}):
            with patch("jose.jwt.decode", side_effect=jwt.ExpiredSignatureError("Expired")):
                with patch.object(sec_mod, "_fetch_jwks", AsyncMock(return_value=mock_jwks)):
                    with pytest.raises(HTTPException) as exc1:
                        await sec_mod._decode_token(tok_rs)
                    assert exc1.value.status_code == 401

                with patch.object(ta_mod, "_fetch_jwks", AsyncMock(return_value=mock_jwks)):
                    with pytest.raises(HTTPException) as exc2:
                        await ta_mod._decode_token(tok_rs)
                    assert exc2.value.status_code == 401

            with patch("jose.jwt.decode", side_effect=ValueError("Invalid RS256 key")):
                with patch.object(sec_mod, "_fetch_jwks", AsyncMock(return_value=mock_jwks)):
                    with pytest.raises(HTTPException) as exc3:
                        await sec_mod._decode_token(tok_rs)
                    assert exc3.value.status_code == 401

                with patch.object(ta_mod, "_fetch_jwks", AsyncMock(return_value=mock_jwks)):
                    with pytest.raises(HTTPException) as exc4:
                        await ta_mod._decode_token(tok_rs)
                    assert exc4.value.status_code == 401

    @pytest.mark.asyncio
    async def test_all_remaining_service_and_tenant_branches(self):
        import asyncio
        from app.core import middleware as mw_mod
        from app.services import notifications as notif_srv

        class DummyRouter(db_mod.DatabaseRouter):
            def get_session(self, hospital_id: str):
                return super().get_session(hospital_id)
        with pytest.raises(NotImplementedError):
            DummyRouter().get_session("h1")

        db_mod._init_engine()
        db_mod._init_engine()
        ctx_dummy = db_mod.HospitalContext("h1", MagicMock())
        db_mod.close_hospital_context(ctx_dummy)

        mock_req_readonly = MagicMock()
        mock_req_readonly.state.tenant = MagicMock(scope="readonly")
        async def fake_call_next_ok(r):
            from starlette.responses import Response
            return Response("OK")
        resp_banner = await mw_mod.ImpersonationBannerMiddleware(MagicMock()).dispatch(mock_req_readonly, fake_call_next_ok)
        assert resp_banner.headers.get("X-Impersonation-Banner") == "true"

        mock_req_post_fail = MagicMock(method="POST")
        mock_req_post_fail.url.path = "/test"
        mock_audit_db = MagicMock()
        mock_audit_db.execute.side_effect = Exception("DB Error")
        with patch("app.core.middleware.get_session_local", return_value=lambda: mock_audit_db):
            await mw_mod.AuditLogMiddleware(MagicMock()).dispatch(mock_req_post_fail, fake_call_next_ok)

        mock_db_sec = MagicMock()
        
        user_rec_a = MagicMock(hospital_id="h-100")
        mock_db_sec.query.return_value.filter.return_value.one_or_none.return_value = user_rec_a
        tok_usr = sec_mod.TokenPayload(sub="u1", preferred_username="u1", email="u1@h.org", realm_access={"roles": ["doctor"]}, raw={"realm_access": {"roles": ["doctor"]}})
        assert await sec_mod.get_current_hospital_id(tok_usr, mock_db_sec) == "h-100"

        user_rec_b = MagicMock(hospital_id=None)
        mock_db_sec.query.return_value.filter.return_value.one_or_none.return_value = user_rec_b
        tok_sa = sec_mod.TokenPayload(sub="u1", preferred_username="u1", email="u1@h.org", realm_access={}, raw={"type": "superadmin", "role": "super_admin"})
        assert await sec_mod.get_current_hospital_id(tok_sa, mock_db_sec) is None

        with pytest.raises(HTTPException) as exc_c:
            await sec_mod.get_current_hospital_id(tok_usr, mock_db_sec)
        assert exc_c.value.status_code == 403

        mock_db_sec.query.return_value.filter.return_value.one_or_none.return_value = None
        with pytest.raises(HTTPException) as exc_d:
            await sec_mod.get_current_hospital_id(tok_usr, mock_db_sec)
        assert exc_d.value.status_code == 403

        assert ta_mod._extract_realm_from_iss("invalid-jwt") is None

        with pytest.raises(HTTPException) as exc_rsa:
            ta_mod._build_rsa_key({"keys": [{"kid": "k-other"}]}, "k-missing")
        assert exc_rsa.value.status_code == 401

        with patch("app.core.tenant_auth.is_tenant_suspended", AsyncMock(return_value=True)):
            tok_tenant = jwt.encode({"sub": "u1", "tenant_id": "t-suspended"}, cfg_mod.settings.secret_key, algorithm="HS256")
            creds_t = MagicMock(scheme="bearer", credentials=tok_tenant)
            req_t = MagicMock()
            with pytest.raises(HTTPException) as exc_susp:
                await ta_mod.get_current_tenant(req_t, creds_t)
            assert exc_susp.value.status_code == 403

        from app.events import subscriber as sub_mod
        with patch("app.events.subscriber.start_consumer", AsyncMock()):
            await sub_mod.start_subscriber()

        mock_t_session = AsyncMock()
        async def fake_session(tid): yield mock_t_session
        with patch("app.events.subscriber.get_tenant_session", fake_session):
            with patch("app.events.subscriber.broadcaster.broadcast", AsyncMock()):
                await sub_mod.handle_announcement_created({"title": "T1", "body": "B1", "target_tenant_ids": ["t1", "t2"], "audience": "specific"})

        from app.services import broadcaster as bcast_mod
        bc = bcast_mod.NotificationBroadcaster()

        class FakePubSubExtra:
            async def subscribe(self, ch): pass
            async def unsubscribe(self, ch): pass
            async def aclose(self): pass
            async def listen(self):
                yield {"type": "subscribe", "data": 1}
                yield {"type": "message", "data": "msg1"}
                raise asyncio.CancelledError()

        mock_redis_extra = MagicMock()
        mock_redis_extra.pubsub.return_value = FakePubSubExtra()
        bc._redis = mock_redis_extra
        q_extra = asyncio.Queue()
        bc._local_queues["t_extra"] = {q_extra}

        await bc._listen("t_extra")

        mock_empty_db = AsyncMock()
        mock_empty_res = MagicMock()
        mock_empty_res.scalars.return_value.all.return_value = []
        mock_empty_db.execute.return_value = mock_empty_res
        res_empty = await notif_srv.mark_all_notifications_read(mock_empty_db, "t1", "u1", "doctor")
        assert res_empty.marked_count == 0

        with patch.object(ts_mod, "_get_redis") as mock_gr:
            mock_r = AsyncMock()
            mock_r.get.return_value = "0"
            mock_gr.return_value = mock_r
            assert await ts_mod.is_tenant_suspended("t1") is False

        # security.py line 180: get_current_active_user with keycloak_introspect=True
        tok_sec180 = jwt.encode({"sub": "u-sec180"}, cfg_mod.settings.secret_key, algorithm="HS256")
        creds_sec180 = MagicMock(scheme="bearer", credentials=tok_sec180)
        req_sec180 = MagicMock()
        mock_intro_succ_resp = MagicMock()
        mock_intro_succ_resp.json.return_value = {"active": True}
        mock_intro_succ_client = AsyncMock()
        mock_intro_succ_client.post.return_value = mock_intro_succ_resp
        mock_intro_succ_client.__aenter__.return_value = mock_intro_succ_client

        with patch.object(cfg_mod.settings, "keycloak_introspect", True):
            with patch("httpx.AsyncClient", return_value=mock_intro_succ_client):
                usr_res = await sec_mod.get_current_active_user(req_sec180, credentials=creds_sec180)
                assert usr_res.sub == "u-sec180"

        # subscriber.py lines 211-213: announcement failure per tenant
        mock_master_db = MagicMock()
        mock_master_db.execute.return_value.fetchall.return_value = [("t-fail-1",)]
        with patch("app.db.master.get_master_db", return_value=mock_master_db):
            with patch("app.events.subscriber.get_tenant_session", side_effect=Exception("Tenant Session Error")):
                await sub_mod.handle_announcement_created({"title": "T1", "body": "B1", "audience": "all"})

        # main.py lines 44-45: lifespan import/task failure
        from app import main as main_mod
        with patch("asyncio.create_task", side_effect=Exception("Task Error")):
            async with main_mod.lifespan(main_mod.app):
                pass

        # tenant_service line 52: cached == "1" returns True
        with patch.object(ts_mod, "_get_redis") as mock_gr2:
            mock_r2 = AsyncMock()
            mock_r2.get.return_value = "1"
            mock_gr2.return_value = mock_r2
            assert await ts_mod.is_tenant_suspended("t1") is True

        # tenant_service line 106: check_and_update_tenant_status not found
        mock_db_none = MagicMock()
        mock_db_none.execute.return_value.one_or_none.return_value = None
        assert await ts_mod.check_and_update_tenant_status(mock_db_none, "t-none") == "not_found"

        # subscriber default tenant_id branches
        with patch("app.events.subscriber.get_tenant_session", fake_session):
            with patch("app.events.subscriber.broadcaster.broadcast", AsyncMock()):
                await sub_mod.handle_subscription_request_created({"tenant_id": "default"})
                await sub_mod.handle_tenant_created({"tenant_id": "default"})

        mock_db_ts = MagicMock()

        tok_intro = "token-for-introspect"
        mock_intro_resp = MagicMock()
        mock_intro_resp.json.return_value = {"active": False}
        mock_intro_client = AsyncMock()
        mock_intro_client.post.return_value = mock_intro_resp
        mock_intro_client.__aenter__.return_value = mock_intro_client

        with patch("httpx.AsyncClient", return_value=mock_intro_client):
            with pytest.raises(HTTPException) as exc_inact:
                await sec_mod._introspect_token(tok_intro)
            assert exc_inact.value.status_code == 401

        with pytest.raises(HTTPException) as exc_inact2:
            await sec_mod._introspect_token(tok_intro)
        assert exc_inact2.value.status_code == 401

        ta_mod._jwks_cache["jwks:temp-realm"] = {"keys": []}
        assert await ta_mod._fetch_jwks("temp-realm") == {"keys": []}
        ta_mod._jwks_cache.clear()

        with patch("app.events.subscriber.get_tenant_session", fake_session):
            with patch("app.events.subscriber.broadcaster.broadcast", AsyncMock()):
                await sub_mod.handle_subscription_request_created({"tenant_id": "t-custom"})
                await sub_mod.handle_tenant_created({"tenant_id": "t-custom", "name": "Custom Hosp"})

        with patch("app.events.subscriber.get_tenant_session", fake_session):
            with patch("app.services.notifications.create_notification", side_effect=Exception("Notification Error")):
                await sub_mod.handle_announcement_created({"title": "T1", "body": "B1", "target_tenant_ids": ["t1"]})

        mock_db_ts.execute.return_value.one_or_none.return_value = ("active", None, 10)
        assert await ts_mod.check_and_update_tenant_status(mock_db_ts, "t1") == "active"

        # tenant_service _get_redis initialization
        ts_mod._redis = None
        with patch("redis.asyncio.from_url", return_value=AsyncMock()):
            r_inst = await ts_mod._get_redis()
            assert r_inst is not None

        sub_end_recent = datetime.now(timezone.utc) - timedelta(days=5)
        mock_db_ts.execute.return_value.one_or_none.return_value = ("active", sub_end_recent, 10)
        assert await ts_mod.check_and_update_tenant_status(mock_db_ts, "t1") == "expired"

        sub_end_old = datetime.now(timezone.utc) - timedelta(days=40)
        mock_db_ts3 = MagicMock()
        mock_db_ts3.execute.return_value.one_or_none.return_value = ("active", sub_end_old, 10)
        with patch("app.services.tenant_service._revoke_keycloak_sessions", AsyncMock()):
            assert await ts_mod.check_and_update_tenant_status(mock_db_ts3, "t1") == "suspended"

        mock_db_dsn = MagicMock()
        enc_dsn = ts_mod.encrypt_dsn("postgresql://user:pass@localhost/db")
        mock_db_dsn.execute.return_value.scalar.return_value = enc_dsn
        assert await ts_mod.get_tenant_db_dsn(mock_db_dsn, "custom_tid") == "postgresql://user:pass@localhost/db"

        from app.messaging import connection as conn_mod
        mock_active_conn = AsyncMock()
        mock_active_conn.is_closed = False
        conn_mod._connection = mock_active_conn
        await conn_mod.close_connection()
        assert conn_mod._connection is None

        mock_resp_tok = MagicMock()
        mock_resp_tok.json.return_value = {"access_token": "admin-tok"}
        mock_resp_fail = MagicMock()
        mock_resp_fail.is_success = False
        mock_client_fail = AsyncMock()
        mock_client_fail.post.return_value = mock_resp_tok
        mock_client_fail.get.return_value = mock_resp_fail
        mock_client_fail.__aenter__.return_value = mock_client_fail

        with patch("httpx.AsyncClient", return_value=mock_client_fail):
            await ts_mod._revoke_keycloak_sessions("t1")

        mock_resp_users_no_uid = MagicMock()
        mock_resp_users_no_uid.is_success = True
        mock_resp_users_no_uid.json.return_value = [{"id": None}]
        mock_client_no_uid = AsyncMock()
        mock_client_no_uid.post.return_value = mock_resp_tok
        mock_client_no_uid.get.return_value = mock_resp_users_no_uid
        mock_client_no_uid.__aenter__.return_value = mock_client_no_uid

        with patch("httpx.AsyncClient", return_value=mock_client_no_uid):
            await ts_mod._revoke_keycloak_sessions("t1")

        with patch.object(ts_mod, "_get_redis") as mock_gr3:
            mock_r3 = AsyncMock()
            mock_r3.get.return_value = "0"
            mock_gr3.return_value = mock_r3
            assert await ts_mod.is_tenant_suspended("t1") is False

        with patch.object(ts_mod, "_get_redis") as mock_gr4:
            mock_r4 = AsyncMock()
            mock_r4.get.return_value = None
            mock_gr4.return_value = mock_r4
            assert await ts_mod.is_tenant_suspended("t-none-cached") is False

        with patch.object(ts_mod, "_get_redis", side_effect=Exception("Redis Error")):
            assert await ts_mod.is_tenant_suspended("t1") is False

        conn_mod._connection = None
        await conn_mod.close_connection()

        from app import main as main_mod
        start_sub_backup = getattr(sub_mod, "start_subscriber", None)
        if hasattr(sub_mod, "start_subscriber"):
            delattr(sub_mod, "start_subscriber")
        try:
            async with main_mod.lifespan(main_mod.app):
                pass
        finally:
            if start_sub_backup:
                setattr(sub_mod, "start_subscriber", start_sub_backup)

        with patch.object(cfg_mod.settings, "allowed_origins", ""):
            import importlib
            import app.main
            importlib.reload(app.main)

        req_empty_orig = MagicMock(headers={})
        resp_fake = MagicMock(headers={})
        async def fake_call_next(req): return resp_fake
        await main_mod.security_headers(req_empty_orig, fake_call_next)

        mock_db_ts.execute.return_value.scalar.return_value = None
        assert await ts_mod.get_tenant_db_dsn(mock_db_ts, "default") == cfg_mod.settings.database_url
        assert await ts_mod.get_tenant_db_dsn(mock_db_ts, "custom") == cfg_mod.settings.database_url

        enc_dsn = ts_mod.encrypt_dsn("postgresql://user:pass@localhost/db")
        mock_db_ts.execute.side_effect = [MagicMock(scalar=lambda: None), MagicMock(scalar=lambda: enc_dsn)]
        assert await ts_mod.get_tenant_db_dsn(mock_db_ts, "custom") == "postgresql://user:pass@localhost/db"

    @pytest.mark.asyncio
    async def test_fetch_jwks_cached(self):
        sec_mod._jwks_cache["jwks:hospital-realm"] = {"keys": []}
        jwks = await sec_mod._fetch_jwks("hospital-realm")
        assert jwks == {"keys": []}

    def test_build_rsa_key(self):
        jwks = {"keys": [{"kid": "k1", "n": "xyz"}]}
        assert sec_mod._build_rsa_key(jwks, "k1") == {"kid": "k1", "n": "xyz"}
        with pytest.raises(HTTPException) as exc:
            sec_mod._build_rsa_key(jwks, "k2")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_introspect_token(self):
        sec_mod._introspection_cache["cached-active"] = True
        await sec_mod._introspect_token("cached-active")

        sec_mod._introspection_cache["cached-inactive"] = False
        with pytest.raises(HTTPException) as exc:
            await sec_mod._introspect_token("cached-inactive")
        assert exc.value.status_code == 401

    def test_extract_roles(self):
        sa_user = sec_mod.TokenPayload(sub="sa", preferred_username="sa", email=None, realm_access={}, raw={"type": "superadmin", "role": "super_admin"})
        assert sec_mod._extract_roles(sa_user) == ["super_admin"]

        reg_user = sec_mod.TokenPayload(sub="u1", preferred_username="u1", email=None, realm_access={"roles": ["doctor"]}, raw={})
        assert sec_mod._extract_roles(reg_user) == ["doctor"]

    @pytest.mark.asyncio
    async def test_require_role_dep(self):
        dep = sec_mod.require_role("doctor")
        user = sec_mod.TokenPayload(sub="u1", preferred_username="u1", email=None, realm_access={"roles": ["doctor"]}, raw={})
        res = await dep(user)
        assert res == user

        user_bad = sec_mod.TokenPayload(sub="u1", preferred_username="u1", email=None, realm_access={"roles": ["nurse"]}, raw={})
        with pytest.raises(HTTPException) as exc:
            await dep(user_bad)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_current_tenant_missing_token(self):
        req = MagicMock(query_params={})
        with pytest.raises(HTTPException) as exc:
            await ta_mod.get_current_tenant(req, credentials=None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_tenant_no_tenant_association(self):
        payload = {"sub": "u1", "preferred_username": "doc", "realm_access": {"roles": ["doctor"]}}
        tok = jwt.encode(payload, cfg_mod.settings.secret_key, algorithm="HS256")
        creds = MagicMock(scheme="bearer", credentials=tok)
        req = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await ta_mod.get_current_tenant(req, credentials=creds)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_tenant_service_all_branches(self):
        mock_db = MagicMock()
        enc_dsn = ts_mod.encrypt_dsn("postgresql://u:p@localhost/hosp")

        mock_db.execute.return_value.scalar.return_value = enc_dsn
        dsn_def = await ts_mod.get_tenant_db_dsn(mock_db, "default")
        assert "postgresql://u:p@localhost/hosp" in dsn_def

        mock_db.execute.return_value.scalar.return_value = None
        dsn_fb = await ts_mod.get_tenant_db_dsn(mock_db, "nonexistent")
        assert dsn_fb == cfg_mod.settings.database_url

        mock_redis = AsyncMock()
        with patch.object(ts_mod, "_get_redis", AsyncMock(return_value=mock_redis)):
            await ts_mod.remove_tenant_suspension_cache("t1")
            mock_redis.delete.assert_awaited_once_with("suspended_tenant:t1")

        mock_db.execute.return_value.one_or_none.return_value = ("active", datetime.now(timezone.utc) + timedelta(days=10))
        assert await ts_mod.check_tenant_subscription(mock_db, "t1") == "active"

        mock_db.execute.return_value.one_or_none.return_value = ("suspended", None)
        assert await ts_mod.check_tenant_subscription(mock_db, "t1") == "suspended"

        mock_db.execute.return_value.one_or_none.return_value = ("active", datetime.now(timezone.utc) - timedelta(days=5))
        assert await ts_mod.check_tenant_subscription(mock_db, "t1") == "expired"

        mock_db.execute.return_value.one_or_none.return_value = None
        assert await ts_mod.check_tenant_subscription(mock_db, "t1") == "not_found"

        mock_db.execute.return_value.one_or_none.return_value = ("active", datetime.now(timezone.utc) - timedelta(days=35), 1)
        with patch.object(ts_mod, "_revoke_keycloak_sessions", AsyncMock()):
            with patch.object(ts_mod, "cache_tenant_suspension", AsyncMock()):
                res = await ts_mod.check_and_update_tenant_status(mock_db, "t1")
                assert res == "suspended"

    @pytest.mark.asyncio
    async def test_tenant_service_redis_exceptions_and_fallbacks(self):
        with patch.object(ts_mod, "_get_redis", side_effect=Exception("Redis Down")):
            res = await ts_mod.is_tenant_suspended("t1")
            assert res is False

            await ts_mod.cache_tenant_suspension("t1")
            await ts_mod.remove_tenant_suspension_cache("t1")

        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("HTTP Error")
        mock_client.__aenter__.return_value = mock_client
        with patch("httpx.AsyncClient", return_value=mock_client):
            await ts_mod._revoke_keycloak_sessions("t1")

    @pytest.mark.asyncio
    async def test_revoke_keycloak_sessions_success(self):
        mock_resp_tok = MagicMock()
        mock_resp_tok.json.return_value = {"access_token": "admin-tok"}
        mock_resp_users = MagicMock()
        mock_resp_users.is_success = True
        mock_resp_users.json.return_value = [{"id": "k-user-1"}]

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp_tok
        mock_client.get.return_value = mock_resp_users
        mock_client.put.return_value = MagicMock()
        mock_client.__aenter__.return_value = mock_client

        with patch("httpx.AsyncClient", return_value=mock_client):
            await ts_mod._revoke_keycloak_sessions("t1")
            assert mock_client.put.call_count >= 1

    @pytest.mark.asyncio
    async def test_tenant_auth_fetch_and_decode_full(self):
        valid_iss = f"{cfg_mod.settings.keycloak_url}/realms/hospital-realm"
        tok = jwt.encode({"iss": valid_iss}, "secret", algorithm="HS256")
        assert ta_mod._extract_realm_from_iss(tok) == "hospital-realm"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"keys": [{"kid": "k1"}]}
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__.return_value = mock_client

        with patch("httpx.AsyncClient", return_value=mock_client):
            data = await ta_mod._fetch_jwks("hospital-realm")
            assert data == {"keys": [{"kid": "k1"}]}

    @pytest.mark.asyncio
    async def test_messaging_and_main_additional_branch_coverage(self):
        from app.messaging import publisher as pub_mod
        from app.messaging import subscriber as sub_msg_mod
        from app import main as main_mod

        with patch("app.messaging.publisher.get_channel", side_effect=Exception("RabbitMQ connection error")):
            await pub_mod.publish_event("test.key", {"data": 1})

        class FakeMessage:
            def __init__(self, body=b"invalid-json"):
                self.body = body
                self.routing_key = "test.key"
            def process(self):
                class AsyncContext:
                    async def __aenter__(self): return None
                    async def __aexit__(self, *args): return None
                return AsyncContext()

        class FakeQueueIter:
            def __init__(self): self.yielded = False
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            def __aiter__(self): return self
            async def __anext__(self):
                if not self.yielded:
                    self.yielded = True
                    return FakeMessage()
                raise StopAsyncIteration

        mock_queue = MagicMock()
        mock_queue.iterator.return_value = FakeQueueIter()
        mock_queue.name = "test_queue"
        mock_queue.bind = AsyncMock()
        mock_channel = AsyncMock()
        mock_channel.declare_queue = AsyncMock(return_value=mock_queue)
        mock_conn = AsyncMock()
        mock_conn.channel = AsyncMock(return_value=mock_channel)

        with patch("app.messaging.subscriber.get_connection", return_value=mock_conn):
            with patch("app.messaging.subscriber.declare_exchange", AsyncMock()):
                await sub_msg_mod.start_consumer("test_srv", ["key1"], AsyncMock())

        with patch.object(cfg_mod.settings, "environment", "prod"):
            req_prod = MagicMock()
            async def fake_call_next(r):
                from starlette.responses import Response
                return Response("OK")
            resp = await main_mod.security_headers(req_prod, fake_call_next)
            assert resp.headers.get("Content-Security-Policy") == "default-src 'none'"

        with patch("app.events.subscriber.start_subscriber", side_effect=Exception("Subscriber start failed")):
            async with main_mod.lifespan(main_mod.app):
                pass

        payload = {
            "sub": "u2",
            "tenant_id": "t-active",
            "preferred_username": "doc2",
            "email": "doc2@hosp.com",
            "realm_access": {"roles": ["doctor"]},
            "scope": "readonly",
        }
        tok_valid = jwt.encode(payload, cfg_mod.settings.secret_key, algorithm="HS256")
        creds = MagicMock(scheme="bearer", credentials=tok_valid)
        req = MagicMock()

        with patch.object(ta_mod, "is_tenant_suspended", AsyncMock(return_value=False)):
            ctx = await ta_mod.get_current_tenant(req, credentials=creds)
            assert ctx.tenant_id == "t-active"
            assert ctx.scope == "readonly"
            assert ctx.user_sub == "u2"


class TestNotificationLifespanAndDBSessions:
    @pytest.mark.asyncio
    async def test_lifespan_context(self):
        from app.main import lifespan, app
        with patch("app.core.database.init_db"):
            with patch("app.services.broadcaster.broadcaster.connect", AsyncMock()):
                with patch("app.services.broadcaster.broadcaster.disconnect", AsyncMock()):
                    with patch("app.events.subscriber.start_subscriber", AsyncMock()):
                        async with lifespan(app):
                            pass

    def test_health_endpoint(self):
        from app.main import app
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def fake_lifespan(app):
            yield
        with patch("app.main.lifespan", fake_lifespan):
            client = TestClient(app)
            resp = client.get("/health")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok", "service": "notification-service"}

    @pytest.mark.asyncio
    async def test_db_session_and_master(self):
        from app.db import session as sess_mod
        from app.db import master as master_mod

        async def fake_tenant_session(t_id):
            yield AsyncMock()

        with patch("app.db.session.get_tenant_session", fake_tenant_session):
            async for s in sess_mod.get_tenant_db("t1"):
                assert s is not None

        with patch("app.db.master.MasterSessionLocal", return_value=MagicMock()):
            db = master_mod.get_master_db()
            db.close()
            with master_mod.get_master_session() as s:
                assert s is not None

class TestNotificationCoreDatabaseAndMiddleware:
    def test_database_module(self):
        from app.core import database as db_mod
        db_mod.init_db()
        sess_fac = db_mod.get_session_local()
        assert sess_fac is not None

        router = db_mod.DefaultDatabaseRouter()
        with patch.object(db_mod, "get_session_local", return_value=MagicMock()):
            sess = router.get_session("h1")
            assert sess is not None

        with patch.object(db_mod, "get_session_local", return_value=MagicMock()):
            gen = db_mod.get_db()
            db_inst = next(gen)
            assert db_inst is not None

        ctx = db_mod.get_hospital_context("h1")
        assert ctx.hospital_id == "h1"
        db_mod.close_hospital_context(ctx)

    def test_limiter_rate_limit_key(self):
        from app.core.limiter import _rate_limit_key
        from starlette.requests import Request
        req_sub = Request({"type": "http", "headers": []})
        req_sub.state.user_sub = "user-123"
        assert _rate_limit_key(req_sub) == "user-123"

        req_no_sub = Request({"type": "http", "client": ("127.0.0.1", 5000), "headers": []})
        assert _rate_limit_key(req_no_sub) == "127.0.0.1"

    @pytest.mark.asyncio
    async def test_middleware_options_and_readonly(self):
        from app.core import middleware as mw_mod

        # AuditLogMiddleware OPTIONS
        audit_mw = mw_mod.AuditLogMiddleware(MagicMock())
        req_opt = MagicMock(method="OPTIONS")
        call_next = AsyncMock(return_value=MagicMock(headers={}))
        await audit_mw.dispatch(req_opt, call_next)

        # AuditLogMiddleware POST with DB execute
        req_post = MagicMock(method="POST")
        req_post.state.user_sub = "sub-1"
        req_post.state.tenant = MagicMock(tenant_id="t1")
        req_post.url.path = "/test"

        mock_db = MagicMock()
        with patch("app.core.middleware.get_session_local", return_value=MagicMock(return_value=mock_db)):
            resp = await audit_mw.dispatch(req_post, call_next)
            assert mock_db.execute.call_count >= 1

        # ReadOnlyScopeMiddleware POST block
        ro_mw = mw_mod.ReadOnlyScopeMiddleware(MagicMock())
        req_write = MagicMock(method="POST")
        req_write.state.tenant = MagicMock(scope="readonly")
        resp_ro = await ro_mw.dispatch(req_write, AsyncMock())
        assert resp_ro.status_code == 403

class TestNotificationMiddlewareDispatch:
    @pytest.mark.asyncio
    async def test_audit_log_middleware(self):
        req = MagicMock(method="GET")
        req.url.path = "/api/v1/notifications"
        req.state.user_sub = "u1"
        req.state.tenant = MagicMock(tenant_id="t1")

        mock_db = MagicMock()
        with patch("app.core.middleware.get_session_local", return_value=MagicMock(return_value=mock_db)):
            resp_mock = MagicMock(headers={}, status_code=200)
            call_next = AsyncMock(return_value=resp_mock)
            mw = mid_mod.AuditLogMiddleware(MagicMock())
            resp = await mw.dispatch(req, call_next)
            assert resp.headers["X-Request-ID"] is not None
