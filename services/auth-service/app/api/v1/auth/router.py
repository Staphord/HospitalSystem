import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.api.v1.auth.schemas import (
    ImpersonateRequest,
    ImpersonateResponse,
    LoginRequest,
    LogoutRequest,
    MFAChallengeResponse,
    MFALoginVerifyRequest,
    MFAVerifyRequest,
    MFAEmailSendLoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    SignupRequest,
    SignupResponse,
    SuperAdminLoginRequest,
    SuperAdminTokenResponse,
    TokenResponse,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.database import get_session_local
from app.core.limiter import limiter
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.core.security import TokenPayload, get_current_active_user
from app.exceptions import BadRequestError
from app.models.admin import SuperAdmin
from app.models.master import GlobalAuditLog, Tenant
from app.services import auth as auth_service
from app.services.impersonation import create_impersonation_token, log_impersonation_event
from app.services.keycloak_realm import setup_tenant_realm, verify_tenant_realm_exists
from app.services.keycloak_admin import (
    create_keycloak_user,
    ensure_roles,
    set_user_attribute,
    create_local_user,
    remove_user_role,
)
from app.services.superadmin_auth import _hash_password
from app.services.brute_force import (
    get_remaining_seconds,
    is_blocked,
    record_failed_attempt,
    record_successful_login,
    get_failed_attempts,
    MAX_FAILED_ATTEMPTS,
)
from app.services.tenant_service import is_tenant_suspended

public_router = APIRouter()
router = APIRouter(dependencies=[Depends(get_current_active_user)])


@public_router.post("/signup", response_model=SignupResponse, status_code=201)
@limiter.limit("5/minute")
async def signup(
    request: Request,
    body: SignupRequest,
    db: Session = Depends(get_db),
) -> dict:
    tenant_id = f"hosp-{uuid.uuid4().hex[:8]}"

    existing = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Tenant already exists")

    from app.services.tenant_service import encrypt_dsn
    chosen_plan = body.subscription_plan.lower()
    chosen_cycle = body.subscription_billing_cycle.lower()
    is_trial = chosen_plan == "free_trial"

    tenant = Tenant(
        tenant_id=tenant_id,
        name=body.hospital_name,
        db_dsn_encrypted=encrypt_dsn(f"postgresql://placeholder@{tenant_id}:5432/{tenant_id}"),
        status="trial" if is_trial else "active",
        subscription_plan=chosen_plan,
        subscription_status="trial" if is_trial else "active",
        subscription_billing_cycle=chosen_cycle,
        subscription_start=datetime.now(timezone.utc),
        subscription_end=datetime.now(timezone.utc) + timedelta(days=14),
        has_used_trial=is_trial,
        is_active=True,
    )
    db.add(tenant)
    db.commit()

    # Create per-tenant Keycloak realm and verify it exists
    try:
        await setup_tenant_realm(tenant_id)
        exists = await verify_tenant_realm_exists(tenant_id)
        if exists:
            tenant.keycloak_realm = tenant_id
        else:
            logger.warning("Realm %s was not actually created after setup, falling back", tenant_id)
            tenant.keycloak_realm = settings.keycloak_realm
        db.commit()
    except Exception as e:
        logger.warning("Failed to setup tenant realm %s: %s", tenant_id, e)
        tenant.keycloak_realm = settings.keycloak_realm
        db.commit()

    realm = tenant.keycloak_realm or settings.keycloak_realm
    await ensure_roles(["hospital_user", "hospital_admin"], realm=realm)

    try:
        kc_sub = await create_keycloak_user(
            username=body.admin_username,
            password=body.admin_password,
            email=body.admin_email,
            roles=["hospital_admin", "hospital_user"],
            full_name=body.admin_full_name or None,
            realm=realm,
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create admin user: {e}",
        )

    await set_user_attribute(kc_sub, "tenant_id", tenant_id, realm=realm)

    # Create tenant database synchronously and store user in tenant DB
    # NO FALLBACK: tenant database MUST be created, or signup fails
    from app.services.provision import provision_tenant_database_sync, get_tenant_db_session
    dsn = provision_tenant_database_sync(tenant_id, body.hospital_name)
    
    # Create local user in the tenant database (not master DB)
    tenant_db = get_tenant_db_session(tenant_id)
    try:
        create_local_user(
            db=tenant_db,
            keycloak_sub=kc_sub,
            username=body.admin_username,
            full_name=body.admin_full_name or None,
            email=body.admin_email,
            role="hospital_admin",
            hospital_id=tenant_id,
        )
    finally:
        tenant_db.close()

    # Publish event for downstream services
    try:
        from app.events.publisher import publish_tenant_created
        await publish_tenant_created(
            tenant_id=tenant_id,
            name=body.hospital_name,
            admin_email=body.admin_email,
            admin_username=body.admin_username,
            hospital_id=tenant_id,
        )
    except Exception as exc:
        logger.warning("Failed to publish tenant.created event: %s", exc)

    result = await auth_service.login(
        username=body.admin_username,
        password=body.admin_password,
        db=db,
        realm=realm,
    )

    return {
        "tenant_id": tenant_id,
        "hospital_name": body.hospital_name,
        "access_token": result["access_token"],
        "refresh_token": result["refresh_token"],
        "expires_in": result["expires_in"],
        "refresh_expires_in": result["refresh_expires_in"],
        "token_type": "Bearer",
    }


@public_router.post("/superadmin/login", response_model=SuperAdminTokenResponse | MFAChallengeResponse)
@limiter.limit("10/minute")
async def superadmin_login(
    request: Request,
    body: SuperAdminLoginRequest,
    db: Session = Depends(get_db),
) -> dict:
    ip = request.client.host if request.client else None
    logger.info("Superadmin login attempt: %s from %s", body.username, ip)

    # Brute-force protection
    if is_blocked(body.username, ip):
        remaining = get_remaining_seconds(body.username, ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "BRUTE_FORCE_BLOCKED",
                "message": f"Too many failed login attempts. Try again in {remaining} seconds.",
                "retry_after": remaining,
            },
        )

    # Try master realm first (new multi-tenant architecture), fall back to
    # default realm for backwards compatibility (existing superadmins).
    realms_to_try = ["master", settings.keycloak_realm]
    last_exc = None
    result = None
    for realm_candidate in realms_to_try:
        try:
            result = await auth_service.login(
                username=body.username,
                password=body.password,
                db=db,
                realm=realm_candidate,
            )
            break
        except (HTTPException, Exception) as exc:
            last_exc = exc
            logger.debug(
                "Superadmin login attempt for %s in realm %s failed: %s",
                body.username, realm_candidate, exc,
            )

    if result is None:
        record_failed_attempt(body.username, ip)
        if isinstance(last_exc, HTTPException):
            if last_exc.status_code == status.HTTP_401_UNAUTHORIZED:
                attempts = get_failed_attempts(body.username, ip)
                remaining = max(0, MAX_FAILED_ATTEMPTS - attempts)
                if attempts >= MAX_FAILED_ATTEMPTS:
                    rem_seconds = get_remaining_seconds(body.username, ip)
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail={
                            "code": "BRUTE_FORCE_BLOCKED",
                            "message": f"Too many failed login attempts. Try again in {rem_seconds} seconds.",
                            "retry_after": rem_seconds,
                        }
                    )
                detail_msg = last_exc.detail if isinstance(last_exc.detail, str) else last_exc.detail.get("message", "Invalid username or password")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "message": detail_msg,
                        "attempts_remaining": remaining
                    },
                    headers=last_exc.headers
                )
            raise last_exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login processing error: {last_exc}",
        ) from last_exc

    # Successful login — clear brute-force counter
    record_successful_login(body.username, ip)

    # Decode access token to verify super_admin role and sync local record
    local_superadmin = None
    try:
        from jose import jwt as _jwt
        token = result["access_token"]
        unverified = _jwt.get_unverified_claims(token)
        roles = unverified.get("realm_access", {}).get("roles", [])
        if "super_admin" not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User is not a super admin",
            )

        # Strip hospital_admin from superadmin users in Keycloak
        if "hospital_admin" in roles:
            try:
                from app.services.keycloak_admin import _headers, _admin_api_url
                import httpx
                hdrs = await _headers()
                url = f"{_admin_api_url(realm_candidate)}/users"
                async with httpx.AsyncClient(timeout=10.0) as c:
                    search = await c.get(f"{url}?username={body.username}", headers=hdrs)
                    if search.is_success and search.json():
                        kc_user_id = search.json()[0]["id"]
                        await remove_user_role(
                            kc_user_id, "hospital_admin", realm=realm_candidate
                        )
                        logger.info(
                            "Stripped hospital_admin role from superadmin %s", body.username
                        )
            except Exception as exc:
                logger.warning("Failed to strip hospital_admin from superadmin: %s", exc)

        # Ensure a matching local super_admin row exists so master-service
        # user management endpoints can list/edit this account.
        import secrets
        email = unverified.get("email") or f"{body.username}@localhost"
        full_name = unverified.get("name") or body.username
        existing = db.query(SuperAdmin).filter(SuperAdmin.username == body.username).first()
        if existing is None:
            existing = db.query(SuperAdmin).filter(SuperAdmin.email == email).first()
        if existing is None:
            admin = SuperAdmin(
                username=body.username,
                email=email,
                password_hash=_hash_password(body.password),
                full_name=full_name,
                role="super_admin",
                mfa_secret=secrets.token_hex(16),
                is_active=True,
                created_at=datetime.now(timezone.utc),
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
            local_superadmin = admin
        else:
            existing.last_login_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(existing)
            local_superadmin = existing
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Error during superadmin database synchronization: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unable to verify super admin privileges",
        )

    if (
        local_superadmin
        and local_superadmin.mfa_enabled
        and auth_service.is_valid_totp_secret(local_superadmin.mfa_secret)
        and local_superadmin.backup_codes
    ):
        import json as _json
        from fastapi.responses import JSONResponse
        from jose import jwt as _jwt

        challenge_payload = {
            "super_admin_id": str(local_superadmin.super_admin_id),
            "mfa_challenge": True,
            "superadmin": True,
            "exp": (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp(),
            "tokens": _json.dumps({
                "access_token": result["access_token"],
                "refresh_token": result["refresh_token"],
                "expires_in": result["expires_in"],
                "refresh_expires_in": result["refresh_expires_in"],
                "token_type": "Bearer",
                "session_id": result.get("session_id", ""),
                "not_before_policy": result.get("not_before_policy", 0),
                "scope": "full",
            }),
        }
        challenge_token = _jwt.encode(
            challenge_payload, settings.secret_key, algorithm="HS256"
        )
        return JSONResponse(
            status_code=202,
            content={"mfa_required": True, "challenge_token": challenge_token},
        )

    ip = request.client.host if request.client else None
    try:
        audit_db = get_session_local()()
        record = GlobalAuditLog(
            user_sub=result.get("session_id", ""),
            action="SUPERADMIN_LOGIN",
            detail=f"SuperAdmin '{body.username}' logged in",
            ip_address=ip,
        )
        audit_db.add(record)
        audit_db.commit()
    except Exception:
        pass
    finally:
        if audit_db:
            audit_db.close()

    return {
        "access_token": result["access_token"],
        "refresh_token": result["refresh_token"],
        "expires_in": result["expires_in"],
        "refresh_expires_in": result["refresh_expires_in"],
        "token_type": "Bearer",
        "session_id": result.get("session_id", ""),
        "not_before_policy": result.get("not_before_policy", 0),
        "scope": "full",
    }


@public_router.post("/login", response_model=TokenResponse | MFAChallengeResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    body: LoginRequest,
    db: Session = Depends(get_db),
) -> dict:
    ip = request.client.host if request.client else None
    logger.info("Login attempt: %s from %s", body.username, ip)

    # Brute-force protection
    if is_blocked(body.username, ip):
        remaining = get_remaining_seconds(body.username, ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "BRUTE_FORCE_BLOCKED",
                "message": f"Too many failed login attempts. Try again in {remaining} seconds.",
                "retry_after": remaining,
            },
        )

    # Determine realm: explicit body parameter > tenant DB lookup > default
    login_realm = body.realm
    if not login_realm:
        # Try to find the user's tenant from Keycloak and resolve the realm
        try:
            from app.services.keycloak_admin import find_user_realm_by_username
            resolved = await find_user_realm_by_username(body.username)
            if resolved:
                login_realm = resolved
        except Exception:
            pass
    if not login_realm:
        login_realm = settings.keycloak_realm

    # If the specified realm doesn't exist in Keycloak, fall back to default
    if login_realm != settings.keycloak_realm:
        try:
            from app.services.keycloak_realm import verify_tenant_realm_exists
            if not await verify_tenant_realm_exists(login_realm):
                logger.warning("Realm %s does not exist, falling back to default", login_realm)
                login_realm = settings.keycloak_realm
        except Exception:
            pass

    try:
        result = await auth_service.login(
            username=body.username,
            password=body.password,
            db=db,
            realm=login_realm,
        )
    except HTTPException as exc:
        record_failed_attempt(body.username, ip)
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            attempts = get_failed_attempts(body.username, ip)
            remaining = max(0, MAX_FAILED_ATTEMPTS - attempts)
            if attempts >= MAX_FAILED_ATTEMPTS:
                rem_seconds = get_remaining_seconds(body.username, ip)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "code": "BRUTE_FORCE_BLOCKED",
                        "message": f"Too many failed login attempts. Try again in {rem_seconds} seconds.",
                        "retry_after": rem_seconds,
                    }
                )
            detail_msg = exc.detail if isinstance(exc.detail, str) else exc.detail.get("message", "Invalid username or password")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "message": detail_msg,
                    "attempts_remaining": remaining
                },
                headers=exc.headers
            )
        raise
    except Exception as exc:
        logger.exception("Login failed for %s: %s", body.username, exc)
        record_failed_attempt(body.username, ip)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login processing error: {exc}",
        ) from exc

    # Successful login — clear brute-force counter
    record_successful_login(body.username, ip)

    # Decode access token to check role — super_admins must use the superadmin portal
    try:
        from jose import jwt as _jwt
        token = result["access_token"]
        unverified = _jwt.get_unverified_claims(token)
        roles = unverified.get("realm_access", {}).get("roles", [])
        if "super_admin" in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Super admin must use the Super Admin Portal",
            )
        result["tenant_id"] = unverified.get("tenant_id")
    except HTTPException:
        raise
    except Exception:
        pass

    try:
        audit_db = get_session_local()()
        record = GlobalAuditLog(
            user_sub=result.get("session_id", ""),
            action="LOGIN",
            detail=f"User '{body.username}' logged in",
            ip_address=ip,
        )
        audit_db.add(record)
        audit_db.commit()
    except Exception:
        pass
    finally:
        if audit_db:
            audit_db.close()

    # Decode token to enforce tenant suspension lockout at login time.
    login_tenant_id = None
    try:
        from jose import jwt as _jwt
        token = result["access_token"]
        unverified = _jwt.get_unverified_claims(token)
        login_tenant_id = unverified.get("tenant_id")
        if login_tenant_id and await is_tenant_suspended(login_tenant_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "TENANT_SUSPENDED", "message": "Tenant subscription is suspended"},
            )
    except HTTPException:
        raise
    except Exception:
        pass

    # ── MFA Challenge Gate ──────────────────────────────────────────────────
    # After Keycloak authenticates the user, check whether MFA is enabled in
    # the tenant database. If it is, return a 202 challenge token instead of
    # the real access/refresh tokens so the frontend can prompt for TOTP.
    try:
        from jose import jwt as _jwt
        from app.services.provision import get_tenant_db_session
        _raw = _jwt.get_unverified_claims(result["access_token"])
        _tenant_id = _raw.get("tenant_id")
        _keycloak_sub = _raw.get("sub")
        if _tenant_id and _keycloak_sub:
            _tdb = get_tenant_db_session(_tenant_id)
            try:
                from app.models.user import User as _User
                _user_rec = _tdb.query(_User).filter(_User.keycloak_sub == _keycloak_sub).first()
                if (
                    _user_rec
                    and _user_rec.mfa_enabled
                    and auth_service.is_valid_totp_secret(_user_rec.mfa_secret)
                    and _user_rec.backup_codes
                ):
                    # Issue a signed, short-lived challenge token that wraps
                    # the full token set — only /mfa/verify-login can unwrap it.
                    import json as _json
                    _challenge_payload = {
                        "sub": _keycloak_sub,
                        "tenant_id": _tenant_id,
                        "mfa_challenge": True,
                        "exp": (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp(),
                        "tokens": _json.dumps(result),
                    }
                    _challenge_token = _jwt.encode(
                        _challenge_payload, settings.secret_key, algorithm="HS256"
                    )
                    from fastapi.responses import JSONResponse
                    return JSONResponse(
                        status_code=202,
                        content={"mfa_required": True, "challenge_token": _challenge_token},
                    )
            finally:
                _tdb.close()
    except Exception as _mfa_exc:
        logger.debug("MFA gate check failed (non-fatal): %s", _mfa_exc)
    # ── End MFA Challenge Gate ──────────────────────────────────────────────

    result["scope"] = "full"
    result["tenant_id"] = login_tenant_id
    return result


@public_router.post("/refresh", response_model=TokenResponse)
@limiter.limit("20/minute")
async def refresh(
    request: Request,
    body: RefreshRequest,
    db: Session = Depends(get_db),
) -> dict:
    result = await auth_service.refresh_access_token(
        refresh_token=body.refresh_token,
        db=db,
    )

    # Enforce tenant suspension lockout on token refresh.
    try:
        from jose import jwt as _jwt
        token = result["access_token"]
        unverified = _jwt.get_unverified_claims(token)
        refresh_tenant_id = unverified.get("tenant_id")
        if refresh_tenant_id and await is_tenant_suspended(refresh_tenant_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "TENANT_SUSPENDED", "message": "Tenant subscription is suspended"},
            )
    except HTTPException:
        raise
    except Exception:
        pass

    result["scope"] = "full"
    try:
        from jose import jwt as _jwt
        result["tenant_id"] = _jwt.get_unverified_claims(result["access_token"]).get("tenant_id")
    except Exception:
        result["tenant_id"] = None
    return result


@public_router.post("/mfa/email/send-login-code", response_model=dict)
@limiter.limit("10/minute")
async def mfa_email_send_login_code(
    request: Request,
    body: MFAEmailSendLoginRequest,
    db: Session = Depends(get_db),
) -> dict:
    from jose import jwt as _jwt
    from app.models.admin import SuperAdmin as _SuperAdmin
    from app.models.user import User as _User
    import pyotp

    # Decode and verify challenge token
    try:
        challenge = _jwt.decode(
            body.challenge_token, settings.secret_key, algorithms=["HS256"]
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired challenge token",
        )

    if not challenge.get("mfa_challenge"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid challenge token context",
        )

    super_admin_id = challenge.get("super_admin_id")
    is_superadmin = super_admin_id is not None

    if is_superadmin:
        mfa_db = db
    else:
        tenant_id = challenge.get("tenant_id")
        if not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tenant context not found in challenge",
            )
        from app.services.provision import get_tenant_db_session
        mfa_db = get_tenant_db_session(tenant_id)

    try:
        # Retrieve user record
        if is_superadmin:
            user_record = mfa_db.query(_SuperAdmin).filter(_SuperAdmin.super_admin_id == super_admin_id).first()
        else:
            keycloak_sub = challenge.get("sub")
            user_record = mfa_db.query(_User).filter(_User.keycloak_sub == keycloak_sub).first()

        if user_record is None or not user_record.mfa_secret:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is not configured for this account",
            )

        # Generate TOTP code
        totp = pyotp.TOTP(user_record.mfa_secret)
        code = totp.now()

        # Send email
        email = user_record.email
        if not email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User email address not found",
            )

        await auth_service.send_mfa_email_code(email=email, code=code)

        return {"detail": "Verification code sent to email"}
    finally:
        if not is_superadmin:
            mfa_db.close()


@public_router.post("/mfa/verify-login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def mfa_verify_login(
    request: Request,
    body: MFALoginVerifyRequest,
    db: Session = Depends(get_db),
) -> dict:
    import json

    from jose import JWTError
    from jose import jwt as _jwt
    from app.models.admin import SuperAdmin as _SuperAdmin
    from app.models.user import User as _User
    from app.services.provision import get_tenant_db_session

    try:
        challenge = _jwt.decode(
            body.challenge_token,
            settings.secret_key,
            algorithms=["HS256"],
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA challenge",
        )

    if not challenge.get("mfa_challenge"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid MFA challenge",
        )

    is_superadmin_challenge = bool(challenge.get("superadmin"))
    tenant_id = challenge.get("tenant_id")
    keycloak_sub = challenge.get("sub")
    super_admin_id = challenge.get("super_admin_id")

    if is_superadmin_challenge:
        if not super_admin_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid MFA challenge",
            )

        user_record = db.query(_SuperAdmin).filter(
            _SuperAdmin.super_admin_id == str(super_admin_id)
        ).first()
        if (
            not user_record
            or not user_record.mfa_enabled
            or not auth_service.is_valid_totp_secret(user_record.mfa_secret)
            or not user_record.backup_codes
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="MFA is not configured for this account",
            )

        # Try TOTP first, then fall back to a backup recovery code
        totp_valid = auth_service.verify_mfa_totp(
            keycloak_sub=str(super_admin_id),
            totp_code=body.totp_code,
            secret=user_record.mfa_secret,
        )
        if not totp_valid:
            backup_valid = auth_service.verify_backup_code(
                user_record=user_record,
                code=body.totp_code,
                db=db,
            )
            if not backup_valid:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid TOTP or backup code",
                )

        tokens_raw = challenge.get("tokens")
        if not isinstance(tokens_raw, str):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid MFA challenge",
            )

        try:
            tokens = json.loads(tokens_raw)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid MFA challenge",
            )
        tokens["scope"] = "full"
        return tokens

    if not tenant_id or not keycloak_sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid MFA challenge",
        )

    tenant_db = get_tenant_db_session(tenant_id)
    try:
        user_record = tenant_db.query(_User).filter(_User.keycloak_sub == keycloak_sub).first()
        if (
            not user_record
            or not user_record.mfa_enabled
            or not auth_service.is_valid_totp_secret(user_record.mfa_secret)
            or not user_record.backup_codes
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="MFA is not configured for this account",
            )

        # Try TOTP first, then fall back to a backup recovery code
        totp_valid = auth_service.verify_mfa_totp(
            keycloak_sub=keycloak_sub,
            totp_code=body.totp_code,
            secret=user_record.mfa_secret,
        )
        if not totp_valid:
            backup_valid = auth_service.verify_backup_code(
                user_record=user_record,
                code=body.totp_code,
                db=tenant_db,
            )
            if not backup_valid:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid TOTP or backup code",
                )

        tokens_raw = challenge.get("tokens")
        if not isinstance(tokens_raw, str):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid MFA challenge",
            )

        tokens = json.loads(tokens_raw)
        tokens["scope"] = "full"
        tokens["tenant_id"] = tenant_id
        return tokens
    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid MFA challenge",
        )
    finally:
        tenant_db.close()


@public_router.post("/password-reset", status_code=202)
@limiter.limit("3/minute")
async def password_reset_request(
    request: Request,
    body: PasswordResetRequest,
    db: Session = Depends(get_db),
) -> dict:
    await auth_service.request_password_reset(
        email=body.email,
        db=db,
    )
    return {"detail": "If the email exists, a reset link has been sent"}


@public_router.post("/password-reset/confirm", status_code=200)
@limiter.limit("5/minute")
async def password_reset_confirm(
    request: Request,
    body: PasswordResetConfirm,
    db: Session = Depends(get_db),
) -> dict:
    await auth_service.confirm_password_reset(
        token=body.token,
        new_password=body.new_password,
        db=db,
    )
    return {"detail": "Password reset successfully"}


@router.post("/logout", status_code=204)
@limiter.limit("30/minute")
async def logout(
    request: Request,
    body: LogoutRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> None:
    await auth_service.logout(
        refresh_token=body.refresh_token,
        db=db,
    )

    ip = request.client.host if request.client else None
    try:
        audit_db = get_session_local()()
        record = GlobalAuditLog(
            user_sub=user.sub,
            action="LOGOUT",
            detail=f"User '{user.preferred_username}' logged out",
            ip_address=ip,
        )
        audit_db.add(record)
        audit_db.commit()
    except Exception:
        pass
    finally:
        if audit_db:
            audit_db.close()


@router.post("/logout-all", status_code=204)
@limiter.limit("10/minute")
async def logout_all(
    request: Request,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> None:
    await auth_service.revoke_all_sessions(
        keycloak_sub=user.sub,
        db=db,
    )


@router.post("/mfa/setup", response_model=dict)
@limiter.limit("10/minute")
async def mfa_setup(
    request: Request,
    user: TokenPayload = Depends(get_current_active_user),
) -> dict:
    return auth_service.generate_mfa_secret(keycloak_sub=user.sub)


@router.post("/mfa/email/send-setup-code", response_model=dict)
@limiter.limit("10/minute")
async def mfa_email_send_setup_code(
    request: Request,
    user: TokenPayload = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> dict:
    from app.models.admin import SuperAdmin as _SuperAdmin
    from app.models.user import User as _User
    from app.exceptions import BadRequestError
    import pyotp

    roles = (user.realm_access or {}).get("roles", [])
    is_superadmin = "super_admin" in roles

    if is_superadmin:
        mfa_db = db
    else:
        tenant_id = user.raw.get("tenant_id") if user.raw else None
        if not tenant_id:
            raise BadRequestError("Tenant context not found")
        from app.services.provision import get_tenant_db_session
        mfa_db = get_tenant_db_session(tenant_id)

    try:
        # Find user email if not in token
        email = user.email
        if not email:
            if is_superadmin:
                user_record = mfa_db.query(_SuperAdmin).filter(_SuperAdmin.super_admin_id == user.sub).first()
            else:
                user_record = mfa_db.query(_User).filter(_User.keycloak_sub == user.sub).first()
            if user_record:
                email = user_record.email

        if not email:
            raise BadRequestError("User email address not found")

        # Generate / get pending secret
        res = auth_service.generate_mfa_secret(keycloak_sub=user.sub)
        secret = res["secret"]

        # Calculate current TOTP token
        totp = pyotp.TOTP(secret)
        code = totp.now()

        # Send email
        await auth_service.send_mfa_email_code(email=email, code=code)

        return {"detail": "Verification code sent to email"}
    finally:
        if not is_superadmin:
            mfa_db.close()



@router.post("/mfa/verify", response_model=dict)
@limiter.limit("10/minute")
async def mfa_verify(
    request: Request,
    body: MFAVerifyRequest,
    user: TokenPayload = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> dict:
    import hashlib
    import json
    from app.models.admin import SuperAdmin as _SuperAdmin
    from app.models.user import User as _User

    roles = (user.realm_access or {}).get("roles", [])
    is_superadmin = "super_admin" in roles

    if is_superadmin:
        mfa_db = db
    else:
        tenant_id = user.raw.get("tenant_id") if user.raw else None
        if not tenant_id:
            raise BadRequestError("Tenant context not found")
        from app.services.provision import get_tenant_db_session
        mfa_db = get_tenant_db_session(tenant_id)

    try:
        pending_secret = auth_service.get_pending_mfa_secret(user.sub)
        valid = auth_service.verify_mfa_totp(
            keycloak_sub=user.sub,
            totp_code=body.totp_code,
            db=mfa_db,
            secret=pending_secret,
        )
        if not valid:
            raise BadRequestError("Invalid TOTP code")

        # Enable MFA on the account and generate backup codes
        if is_superadmin:
            user_record = mfa_db.query(_SuperAdmin).filter(_SuperAdmin.super_admin_id == user.sub).first()
            if user_record is None:
                user_record = mfa_db.query(_SuperAdmin).filter(
                    (_SuperAdmin.email == user.email) |
                    (_SuperAdmin.username == user.preferred_username)
                ).first()
        else:
            user_record = mfa_db.query(_User).filter(_User.keycloak_sub == user.sub).first()

        if user_record is None:
            raise BadRequestError("User record not found")

        import secrets
        codes = [secrets.token_hex(4) for _ in range(10)]
        hashed_codes = [hashlib.sha256(c.encode()).hexdigest() for c in codes]

        user_record.mfa_enabled = True
        if pending_secret:
            user_record.mfa_secret = pending_secret
            auth_service.clear_pending_mfa_secret(user.sub)
        user_record.backup_codes = json.dumps(hashed_codes)
        mfa_db.add(user_record)
        mfa_db.commit()

        return {
            "detail": "MFA verified and enabled successfully",
            "backup_codes": codes,
        }
    finally:
        if not is_superadmin:
            mfa_db.close()


@router.post("/mfa/disable", response_model=dict)
@limiter.limit("10/minute")
async def mfa_disable(
    request: Request,
    user: TokenPayload = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> dict:
    from app.models.admin import SuperAdmin as _SuperAdmin
    from app.models.user import User as _User

    roles = (user.realm_access or {}).get("roles", [])
    is_superadmin = "super_admin" in roles

    if is_superadmin:
        mfa_db = db
    else:
        tenant_id = user.raw.get("tenant_id") if user.raw else None
        if not tenant_id:
            raise BadRequestError("Tenant context not found")
        from app.services.provision import get_tenant_db_session
        mfa_db = get_tenant_db_session(tenant_id)

    try:
        if is_superadmin:
            user_record = mfa_db.query(_SuperAdmin).filter(
                (_SuperAdmin.super_admin_id == user.sub) |
                (_SuperAdmin.email == user.email) |
                (_SuperAdmin.username == user.preferred_username)
            ).first()
        else:
            user_record = mfa_db.query(_User).filter(_User.keycloak_sub == user.sub).first()

        if user_record is None:
            raise BadRequestError("User record not found")

        user_record.mfa_enabled = False
        user_record.mfa_secret = None
        user_record.backup_codes = None
        mfa_db.add(user_record)
        mfa_db.commit()

        return {"detail": "Two-factor authentication disabled successfully"}
    finally:
        if not is_superadmin:
            mfa_db.close()


@router.post("/impersonate", response_model=ImpersonateResponse)
@limiter.limit("10/minute")
async def impersonate(
    request: Request,
    body: ImpersonateRequest,
    user: TokenPayload = Depends(get_current_active_user),
) -> dict:
    roles = user.realm_access.get("roles", []) if user.realm_access else []
    if "super_admin" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super admins can impersonate tenants",
        )

    result = create_impersonation_token(
        super_admin_sub=user.sub,
        super_admin_username=user.preferred_username or "unknown",
        target_tenant_id=body.target_tenant_id,
    )

    ip = request.client.host if request.client else None
    await log_impersonation_event(
        action="IMPERSONATION_START",
        super_admin_sub=user.sub,
        target_tenant_id=body.target_tenant_id,
        ip_address=ip,
    )

    return result


@router.get("/session-check")
async def check_session(
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_db),
):
    from app.models.auth import RefreshToken
    user_sub = ctx.user_sub
    raw = ctx.raw_token or {}
    session_state = raw.get("session_state") or raw.get("sid")

    if not user_sub or not session_state:
        return {"has_other_active": False, "session_revoked": False}

    # 1. Check if the current session itself has any active (unrevoked and unexpired) token
    now = datetime.now(timezone.utc)
    current_active = db.query(RefreshToken).filter(
        RefreshToken.session_id == session_state,
        RefreshToken.keycloak_sub == user_sub,
        RefreshToken.is_revoked == False,
        RefreshToken.expires_at > now,
    ).first()

    if not current_active:
        return {"has_other_active": False, "session_revoked": True}

    # 2. Check if there are other active sessions
    other_active = db.query(RefreshToken).filter(
        RefreshToken.keycloak_sub == user_sub,
        RefreshToken.session_id != session_state,
        RefreshToken.is_revoked == False,
        RefreshToken.expires_at > now,
    ).first()

    return {"has_other_active": other_active is not None, "session_revoked": False}


@router.post("/session-keep-only")
async def keep_only_this_session(
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_db),
):
    from app.models.auth import RefreshToken
    user_sub = ctx.user_sub
    raw = ctx.raw_token or {}
    session_state = raw.get("session_state") or raw.get("sid")

    if user_sub and session_state:
        db.query(RefreshToken).filter(
            RefreshToken.keycloak_sub == user_sub,
            RefreshToken.session_id != session_state,
            RefreshToken.is_revoked == False,
        ).update({"is_revoked": True})
        db.commit()

    return {"detail": "Other sessions invalidated successfully"}
