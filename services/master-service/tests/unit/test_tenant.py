from app.services.tenant_service import decrypt_dsn, encrypt_dsn


def test_tenant_dsn_round_trip() -> None:
    dsn = "postgresql://tenant_user:secret@db.example/tenant_a"

    encrypted = encrypt_dsn(dsn)

    assert encrypted != dsn
    assert decrypt_dsn(encrypted) == dsn
