from datetime import datetime, timezone
from typing import Any

import httpx
import streamlit as st

API_BASE = "http://localhost:8000/api/v1"

st.set_page_config(page_title="Hospital Flow", layout="wide")


# ---------------------------------------------------------------------------
# Low-level HTTP client
# ---------------------------------------------------------------------------


def _client() -> httpx.Client:
    headers = {}
    token = st.session_state.get("access_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=API_BASE, headers=headers, timeout=15.0)


def _error_detail(r: httpx.Response) -> str:
    try:
        body = r.json()
        detail = body.get("detail", r.text) if isinstance(body, dict) else r.text
    except Exception:
        detail = r.text if r.text else f"HTTP {r.status_code}"
    return detail


def _extract_error_code(r: httpx.Response) -> str | None:
    try:
        body = r.json()
        if isinstance(body, dict):
            if "code" in body:
                return body.get("code")
            detail = body.get("detail")
            if isinstance(detail, dict):
                return detail.get("code")
            if isinstance(detail, str):
                return detail
    except Exception:
        pass
    return None


def _is_suspended_response(r: httpx.Response) -> str | None:
    if r.status_code != 403:
        return None
    code = _extract_error_code(r)
    if code in ("TENANT_SUSPENDED", "TENANT_TERMINATED"):
        return code
    return None


def _try_refresh() -> bool:
    refresh_token = st.session_state.get("refresh_token")
    if not refresh_token:
        return False
    try:
        r = _client().post("/auth/refresh", json={"refresh_token": refresh_token})
        if r.status_code == 200:
            data = r.json()
            st.session_state["access_token"] = data["access_token"]
            st.session_state["refresh_token"] = data["refresh_token"]
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _login(username: str, password: str) -> dict | str:
    r = _client().post("/auth/login", json={"username": username, "password": password})
    suspended = _is_suspended_response(r)
    if suspended:
        st.session_state["tenant_lockout"] = {
            "code": suspended,
            "message": _error_detail(r),
        }
        return {"_suspended": True}
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _login_superadmin(username: str, password: str) -> dict | str:
    r = _client().post("/auth/superadmin/login", json={"username": username, "password": password})
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _signup(
    hospital_name: str,
    admin_username: str,
    admin_password: str,
    admin_email: str,
    admin_full_name: str = "",
    subscription_plan: str = "free_trial",
    subscription_billing_cycle: str = "monthly",
) -> dict | str:
    r = _client().post(
        "/auth/signup",
        json={
            "hospital_name": hospital_name,
            "admin_username": admin_username,
            "admin_password": admin_password,
            "admin_email": admin_email,
            "admin_full_name": admin_full_name,
            "subscription_plan": subscription_plan,
            "subscription_billing_cycle": subscription_billing_cycle,
        },
    )
    if r.status_code == 201:
        return r.json()
    return _error_detail(r)


def _get_me() -> dict | None:
    r = _client().get("/me")
    suspended = _is_suspended_response(r)
    if suspended:
        st.session_state["tenant_lockout"] = {
            "code": suspended,
            "message": _error_detail(r),
        }
        return None
    if r.status_code == 401:
        if _try_refresh():
            r = _client().get("/me")
            if r.status_code == 200:
                return r.json()
        return None
    if r.status_code == 200:
        return r.json()
    return None


def _logout() -> None:
    refresh_token = st.session_state.get("refresh_token")
    if refresh_token:
        try:
            _client().post("/auth/logout", json={"refresh_token": refresh_token})
        except Exception:
            pass
    for key in ["access_token", "refresh_token", "username", "session_id", "tenant_id", "me", "tenant_lockout"]:
        st.session_state.pop(key, None)
    st.rerun()


# ---------------------------------------------------------------------------
# Admin service helpers (hospital admin user CRUD)
# ---------------------------------------------------------------------------


def _get_users() -> list[dict] | str:
    r = _client().get("/admin/users")
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _create_user(
    username: str,
    password: str,
    email: str,
    role: str,
    full_name: str = "",
) -> dict | str:
    r = _client().post(
        "/admin/users",
        json={
            "username": username,
            "password": password,
            "email": email,
            "full_name": full_name,
            "role": role,
        },
    )
    if r.status_code == 201:
        return r.json()
    return _error_detail(r)


def _update_user(
    sub: str,
    username: str | None = None,
    full_name: str | None = None,
    password: str | None = None,
    email: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> bool | str:
    payload: dict[str, Any] = {}
    if username is not None:
        payload["username"] = username
    if full_name is not None:
        payload["full_name"] = full_name
    if password is not None:
        payload["password"] = password
    if email is not None:
        payload["email"] = email
    if role is not None:
        payload["role"] = role
    if is_active is not None:
        payload["is_active"] = is_active
    r = _client().patch(f"/admin/users/{sub}", json=payload)
    if r.status_code == 200:
        return True
    return _error_detail(r)


def _delete_user(sub: str) -> bool:
    r = _client().delete(f"/admin/users/{sub}")
    return r.status_code == 204


# ---------------------------------------------------------------------------
# Master service helpers (super admin users)
# ---------------------------------------------------------------------------


def _get_all_users() -> list[dict] | str:
    r = _client().get("/superadmin/users")
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


SA_ROLES = ["super_admin", "billing_manager", "support"]


def _create_superuser(
    username: str,
    password: str,
    email: str,
    full_name: str = "",
    role: str = "super_admin",
    mfa_secret: str = "",
) -> dict | str:
    r = _client().post(
        "/superadmin/users",
        json={
            "username": username,
            "password": password,
            "email": email,
            "full_name": full_name or username,
            "role": role,
            "mfa_secret": mfa_secret or None,
        },
    )
    if r.status_code == 201:
        return r.json()
    return _error_detail(r)


def _update_superuser(
    super_admin_id: str,
    username: str | None = None,
    full_name: str | None = None,
    password: str | None = None,
    email: str | None = None,
    role: str | None = None,
    mfa_secret: str | None = None,
    is_active: bool | None = None,
) -> bool | str:
    payload: dict[str, Any] = {}
    if username is not None:
        payload["username"] = username
    if full_name is not None:
        payload["full_name"] = full_name
    if password is not None:
        payload["password"] = password
    if email is not None:
        payload["email"] = email
    if role is not None:
        payload["role"] = role
    if mfa_secret is not None:
        payload["mfa_secret"] = mfa_secret
    if is_active is not None:
        payload["is_active"] = is_active
    r = _client().patch(f"/superadmin/users/{super_admin_id}", json=payload)
    if r.status_code == 200:
        return True
    return _error_detail(r)


def _delete_superuser(username: str) -> bool:
    r = _client().delete("/superadmin/users", json={"username": username})
    return r.status_code == 204


# ---------------------------------------------------------------------------
# Master service helpers (tenants + subscription lifecycle)
# ---------------------------------------------------------------------------


def _get_tenants() -> list[dict]:
    r = _client().get("/superadmin/tenants")
    if r.status_code == 200:
        return r.json()
    return []


def _create_tenant(
    hospital_name: str,
    admin_username: str,
    admin_password: str,
    admin_email: str,
    plan: str,
    admin_full_name: str = "",
    billing_cycle: str = "monthly",
    **kwargs: Any,
) -> dict | str:
    payload: dict[str, Any] = {
        "hospital_name": hospital_name,
        "admin_full_name": admin_full_name,
        "admin_username": admin_username,
        "admin_password": admin_password,
        "admin_email": admin_email,
        "subscription_plan": plan,
        "subscription_billing_cycle": billing_cycle,
    }
    payload.update({k: v for k, v in kwargs.items() if v is not None and v != ""})
    r = _client().post("/superadmin/tenants", json=payload)
    if r.status_code == 201:
        return r.json()
    return _error_detail(r)


def _update_tenant(tenant_id: str, **kwargs: Any) -> bool | str:
    payload = {k: v for k, v in kwargs.items() if v is not None}
    if not payload:
        return True
    r = _client().patch(f"/superadmin/tenants/{tenant_id}", json=payload)
    if r.status_code == 200:
        return True
    return _error_detail(r)


def _get_subscription_state(tenant_id: str) -> dict | None:
    r = _client().get(f"/superadmin/tenants/{tenant_id}/subscription")
    if r.status_code == 200:
        return r.json()
    return None


def _subscribe_tenant(
    tenant_id: str,
    plan: str,
    billing_cycle: str = "monthly",
    start_trial: bool = False,
) -> dict | str:
    r = _client().post(
        f"/superadmin/tenants/{tenant_id}/subscribe",
        json={"plan": plan, "billing_cycle": billing_cycle, "start_trial": start_trial},
    )
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _upgrade_tenant(
    tenant_id: str,
    plan: str,
    billing_cycle: str | None = None,
) -> dict | str:
    payload: dict[str, Any] = {"plan": plan}
    if billing_cycle:
        payload["billing_cycle"] = billing_cycle
    r = _client().post(f"/superadmin/tenants/{tenant_id}/upgrade", json=payload)
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _downgrade_tenant(
    tenant_id: str,
    plan: str,
    billing_cycle: str | None = None,
    effective_at_end: bool = False,
) -> dict | str:
    payload: dict[str, Any] = {"plan": plan, "effective_at_end": effective_at_end}
    if billing_cycle:
        payload["billing_cycle"] = billing_cycle
    r = _client().post(f"/superadmin/tenants/{tenant_id}/downgrade", json=payload)
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _renew_tenant(tenant_id: str, billing_cycle: str | None = None) -> dict | str:
    payload: dict[str, Any] = {}
    if billing_cycle:
        payload["billing_cycle"] = billing_cycle
    r = _client().post(f"/superadmin/tenants/{tenant_id}/renew", json=payload)
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _activate_tenant(tenant_id: str) -> dict | str:
    r = _client().post(f"/superadmin/tenants/{tenant_id}/activate")
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _suspend_tenant(tenant_id: str, reason: str) -> dict | str:
    r = _client().post(f"/superadmin/tenants/{tenant_id}/suspend", json={"reason": reason})
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _reactivate_tenant(tenant_id: str) -> dict | str:
    r = _client().post(f"/superadmin/tenants/{tenant_id}/reactivate")
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _terminate_tenant(tenant_id: str, reason: str) -> dict | str:
    r = _client().post(f"/superadmin/tenants/{tenant_id}/terminate", json={"reason": reason})
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _get_tenant_subscriptions(tenant_id: str) -> list[dict]:
    r = _client().get(f"/superadmin/tenants/{tenant_id}/subscriptions")
    if r.status_code == 200:
        return r.json()
    return []


def _get_my_subscription() -> dict | None:
    r = _client().get("/tenant/subscription")
    suspended = _is_suspended_response(r)
    if suspended:
        st.session_state["tenant_lockout"] = {
            "code": suspended,
            "message": _error_detail(r),
        }
        return None
    if r.status_code == 200:
        return r.json()
    return None


def _get_my_announcements() -> list[dict]:
    r = _client().get("/tenant/announcements")
    if r.status_code == 200:
        return r.json()
    return []


def _get_subscription_audit_log(tenant_id: str) -> list[dict]:
    r = _client().get(f"/superadmin/tenants/{tenant_id}/subscription-audit-log")
    if r.status_code == 200:
        return r.json()
    return []


def _get_system_health() -> dict:
    r = _client().get("/superadmin/health")
    if r.status_code == 200:
        return r.json()
    return {}


def _get_tenant_stats(tenant_id: str) -> dict | None:
    r = _client().get(f"/superadmin/tenants/{tenant_id}/stats")
    if r.status_code == 200:
        return r.json()
    return None


def _upgrade_my_subscription(plan: str, billing_cycle: str | None = None) -> dict | str:
    payload: dict[str, Any] = {"plan": plan}
    if billing_cycle:
        payload["billing_cycle"] = billing_cycle
    r = _client().post("/tenant/subscription/upgrade", json=payload)
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _downgrade_my_subscription(plan: str, billing_cycle: str | None = None) -> dict | str:
    payload: dict[str, Any] = {"plan": plan}
    if billing_cycle:
        payload["billing_cycle"] = billing_cycle
    r = _client().post("/tenant/subscription/downgrade", json=payload)
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


# ---------------------------------------------------------------------------
# Master service helpers (plan catalog + announcements)
# ---------------------------------------------------------------------------


def _get_plan_catalog() -> list[dict]:
    r = _client().get("/superadmin/plans")
    if r.status_code == 200:
        return r.json()
    return []


def _get_subscription_plans() -> list[dict]:
    r = _client().get("/superadmin/subscription-plans")
    if r.status_code == 200:
        return r.json()
    return []


def _create_plan(
    plan_name: str,
    description: str | None = None,
    max_users: int | None = None,
    monthly_price: float = 0,
    annual_price: float = 0,
    is_active: bool = True,
    **kwargs: Any,
) -> dict | str:
    payload: dict[str, Any] = {
        "plan_name": plan_name,
        "monthly_price": monthly_price,
        "annual_price": annual_price,
        "is_active": is_active,
    }
    if description is not None:
        payload["description"] = description
    if max_users is not None:
        payload["max_users"] = max_users
    payload.update({k: v for k, v in kwargs.items() if v is not None})
    r = _client().post("/superadmin/plans", json=payload)
    if r.status_code == 201:
        return r.json()
    return _error_detail(r)


def _update_plan(plan_id: str, **kwargs: Any) -> dict | str:
    payload = {k: v for k, v in kwargs.items() if v is not None}
    if not payload:
        return True
    r = _client().patch(f"/superadmin/plans/{plan_id}", json=payload)
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _delete_plan(plan_id: str) -> bool:
    r = _client().delete(f"/superadmin/plans/{plan_id}")
    return r.status_code == 204


def _get_announcements() -> list[dict]:
    r = _client().get("/superadmin/announcements")
    if r.status_code == 200:
        return r.json()
    return []


def _create_announcement(
    title: str,
    body: str,
    audience: str = "all",
    target_tenant_ids: list[str] | None = None,
    publish_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict | str:
    if publish_at is None:
        publish_at = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "title": title,
        "body": body,
        "audience": audience,
        "publish_at": publish_at.isoformat(),
    }
    if target_tenant_ids:
        payload["target_tenant_ids"] = target_tenant_ids
    if expires_at:
        payload["expires_at"] = expires_at.isoformat()
    r = _client().post("/superadmin/announcements", json=payload)
    if r.status_code == 201:
        return r.json()
    return _error_detail(r)


def _update_announcement(
    announcement_id: str,
    title: str | None = None,
    body: str | None = None,
    audience: str | None = None,
    target_tenant_ids: list[str] | None = None,
    publish_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict | str:
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if audience is not None:
        payload["audience"] = audience
    if target_tenant_ids is not None:
        payload["target_tenant_ids"] = target_tenant_ids
    if publish_at is not None:
        payload["publish_at"] = publish_at.isoformat()
    if expires_at is not None:
        payload["expires_at"] = expires_at.isoformat()
    if not payload:
        return True
    r = _client().patch(f"/superadmin/announcements/{announcement_id}", json=payload)
    if r.status_code == 200:
        return r.json()
    return _error_detail(r)


def _delete_announcement(announcement_id: str) -> bool:
    r = _client().delete(f"/superadmin/announcements/{announcement_id}")
    return r.status_code == 204


# ---------------------------------------------------------------------------
# UI: Login / Signup
# ---------------------------------------------------------------------------


def _show_login():
    st.title("Hospital Flow")
    st.subheader("Sign In")

    login_type = st.radio(
        "Portal",
        ["Hospital Portal", "Super Admin Portal", "Register New Hospital"],
        horizontal=True,
    )

    if login_type == "Register New Hospital":
        _show_signup()
        return

    with st.form("login_form"):
        username = st.text_input("Username", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")
        submitted = st.form_submit_button("Sign In", use_container_width=True, key="login_signin_btn")

    if submitted:
        if not username or not password:
            st.error("Please enter username and password")
            return

        if login_type == "Super Admin Portal":
            result = _login_superadmin(username, password)
            if isinstance(result, dict):
                st.session_state["access_token"] = result["access_token"]
                st.session_state["refresh_token"] = result.get("refresh_token", "")
                st.session_state["username"] = username
                st.session_state["session_id"] = ""
                st.session_state["tenant_id"] = None
                st.rerun()
            else:
                st.error(result if isinstance(result, str) else "Invalid superadmin credentials")
                return
        else:
            result = _login(username, password)
            if isinstance(result, dict):
                if result.get("_suspended"):
                    st.rerun()
                st.session_state["access_token"] = result["access_token"]
                st.session_state["refresh_token"] = result.get("refresh_token", "")
                st.session_state["username"] = username
                st.session_state["session_id"] = result.get("session_id", "")
                st.session_state["tenant_id"] = result.get("tenant_id")
                st.rerun()
            else:
                st.error(result if isinstance(result, str) else "Invalid credentials")
                return


def _show_signup():
    st.title("Hospital Flow")
    st.subheader("Register Your Hospital")

    plan_slugs = ["free_trial", "basic", "standard", "premium", "enterprise"]
    plan_display = {
        "free_trial": "Free Trial",
        "basic": "Basic",
        "standard": "Standard",
        "premium": "Premium",
        "enterprise": "Enterprise",
    }

    with st.form("signup_form"):
        hospital_name = st.text_input("Hospital Name")
        admin_full_name = st.text_input("Admin Full Name")
        admin_username = st.text_input("Admin Username")
        admin_email = st.text_input("Admin Email")
        admin_password = st.text_input("Admin Password", type="password")
        admin_password_confirm = st.text_input("Confirm Password", type="password")

        st.markdown("---")
        st.caption("Choose your subscription plan")
        plan = st.selectbox(
            "Subscription Plan",
            plan_slugs,
            index=0,
            format_func=lambda x: plan_display.get(x, x),
        )
        billing_cycle = st.selectbox("Billing Cycle", ["monthly", "annual"])

        col1, col2 = st.columns(2)
        with col1:
            submitted = st.form_submit_button("Register", use_container_width=True, key="signup_register_btn")
        with col2:
            back_to_login = st.form_submit_button("Back to Login", use_container_width=True, key="signup_back_btn")

    if back_to_login:
        st.session_state["page"] = "login"
        st.rerun()

    if submitted:
        if not all([hospital_name, admin_username, admin_email, admin_password]):
            st.error("All fields are required")
            return
        if admin_password != admin_password_confirm:
            st.error("Passwords do not match")
            return
        if len(admin_password) < 8:
            st.error("Password must be at least 8 characters")
            return

        result = _signup(
            hospital_name,
            admin_username,
            admin_password,
            admin_email,
            admin_full_name,
            subscription_plan=plan,
            subscription_billing_cycle=billing_cycle,
        )
        if isinstance(result, dict):
            st.session_state["access_token"] = result["access_token"]
            st.session_state["refresh_token"] = result["refresh_token"]
            st.session_state["username"] = admin_username
            st.session_state["session_id"] = ""
            st.session_state["tenant_id"] = result["tenant_id"]
            st.success(f"Hospital '{hospital_name}' registered! Tenant ID: {result['tenant_id']}")
            st.session_state.pop("page", None)
            st.rerun()
        else:
            st.error(f"Registration failed: {result}")


# ---------------------------------------------------------------------------
# UI: User management
# ---------------------------------------------------------------------------

ALL_ROLES = ["hospital_admin", "nurse", "clinician", "doctor", "patient"]


def _role_index(role: str) -> int:
    return ALL_ROLES.index(role) if role in ALL_ROLES else 0


def _show_users_page():
    me = st.session_state.get("me", {})
    is_super = me.get("is_super_admin", False)
    role = me.get("role", "hospital_user")
    tenant_id = me.get("tenant_id") or "N/A"

    # Role guard: only admins may manage users
    if not is_super and role != "hospital_admin":
        st.warning("You do not have permission to manage users.")
        return

    if is_super:
        st.subheader("Super Admin Management")
        users = _get_all_users()
        if isinstance(users, str):
            st.error(f"Failed to load super admins: {users}")
            return

        if users:
            st.markdown("#### All Fields: super_admin_id | full_name | username | email | role | is_active | last_login | created")
            for u in users:
                sid = u.get("super_admin_id", "")
                edit_key = f"sa_edit_{sid}"
                delete_key = f"sa_delete_{sid}"

                cols = st.columns([2, 2, 2, 2, 1.5, 1, 1, 1])
                cols[0].write(str(u.get("super_admin_id", ""))[:8] + "...")
                cols[1].write(u.get("full_name", ""))
                cols[2].write(u.get("username", ""))
                cols[3].write(u.get("email", ""))
                cols[4].write(u.get("role", ""))
                active = u.get("is_active", True)
                cols[5].write("Active" if active else "Inactive")

                if cols[6].button("Edit", key=f"sa_edit_btn_{sid}"):
                    st.session_state[edit_key] = not st.session_state.get(edit_key, False)
                if cols[7].button("Delete", key=f"sa_del_btn_{sid}"):
                    st.session_state[delete_key] = not st.session_state.get(delete_key, False)

                # Show extra details
                with st.expander(f"Details for {u.get('username', '')}", expanded=False):
                    st.write(f"**super_admin_id:** `{sid}`")
                    st.write(f"**last_login_at:** {u.get('last_login_at', 'Never')}")
                    st.write(f"**created_at:** {u.get('created_at', 'N/A')}")
                    st.write(f"**mfa_secret:** `{u.get('mfa_secret', 'N/A')}`")

                if st.session_state.get(edit_key, False):
                    with st.container(border=True):
                        st.markdown(f"**Editing:** {u.get('username', '')}")
                        with st.form(f"sa_edit_form_{sid}"):
                            new_full_name = st.text_input("Full Name", value=u.get("full_name", ""))
                            new_username = st.text_input("Username", value=u.get("username", ""))
                            new_email = st.text_input("Email", value=u.get("email", ""))
                            new_password = st.text_input("New Password (leave blank to keep)", type="password")
                            new_role = st.selectbox(
                                "Role",
                                SA_ROLES,
                                index=SA_ROLES.index(u.get("role", "super_admin")) if u.get("role") in SA_ROLES else 0,
                            )
                            new_mfa = st.text_input("MFA Secret", value=u.get("mfa_secret", ""))
                            new_active = st.checkbox("Is Active", value=u.get("is_active", True))
                            col1, col2 = st.columns(2)
                            with col1:
                                save = st.form_submit_button("Save", use_container_width=True)
                            with col2:
                                cancel = st.form_submit_button("Cancel", use_container_width=True)
                        if save:
                            kwargs: dict[str, Any] = {
                                "super_admin_id": sid,
                                "full_name": new_full_name,
                                "username": new_username,
                                "email": new_email,
                                "role": new_role,
                                "mfa_secret": new_mfa,
                                "is_active": new_active,
                            }
                            if new_password:
                                kwargs["password"] = new_password
                            result = _update_superuser(**kwargs)
                            if result is True:
                                st.success("Super admin updated!")
                                st.session_state[edit_key] = False
                                st.rerun()
                            else:
                                st.error(f"Failed to update: {result}")
                        if cancel:
                            st.session_state[edit_key] = False
                            st.rerun()

                if st.session_state.get(delete_key, False):
                    uname = u.get("username", "")
                    st.warning(f"Delete super admin **{uname}**?")
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("Yes, delete", key=f"sa_confirm_del_{sid}"):
                            if _delete_superuser(uname):
                                st.success("Super admin deleted!")
                                st.session_state[delete_key] = False
                                st.rerun()
                            else:
                                st.error("Failed to delete")
                    with col2:
                        if st.button("Cancel", key=f"sa_cancel_del_{sid}"):
                            st.session_state[delete_key] = False
                            st.rerun()
                st.divider()
        else:
            st.info("No super admin users found")

        st.markdown("### Create New Super Admin")
        with st.form("sa_create_form"):
            new_full_name = st.text_input("Full Name *")
            new_username = st.text_input("Username *")
            new_email = st.text_input("Email *")
            new_password = st.text_input("Password *", type="password")
            new_role = st.selectbox("Role", SA_ROLES, index=0)
            new_mfa = st.text_input("MFA Secret (optional)")
            col1, col2 = st.columns([1, 3])
            with col1:
                create_clicked = st.form_submit_button("Create Super Admin", use_container_width=True)

        if create_clicked:
            if not all([new_username, new_email, new_password]):
                st.error("Username, Email and Password are required")
            elif len(new_password) < 8:
                st.error("Password must be at least 8 characters")
            else:
                result = _create_superuser(
                    new_username,
                    new_password,
                    new_email,
                    full_name=new_full_name,
                    role=new_role,
                    mfa_secret=new_mfa,
                )
                if isinstance(result, dict):
                    st.success(f"Super admin '{new_username}' created!")
                    st.rerun()
                else:
                    st.error(f"Failed to create super admin: {result}")
        return

    st.subheader("User Management")
    st.markdown(f"Managing users for tenant: **{tenant_id}**")

    users = _get_users()
    if isinstance(users, str):
        st.error(f"Failed to load users: {users}")
        return

    if users:
        st.markdown("### Users")
        for u in users:
            sub = u.get("keycloak_sub", "")
            edit_key = f"edit_{sub}"
            delete_key = f"delete_{sub}"

            is_active = u.get("is_active", True)
            cols = st.columns([2.5, 2.5, 2.5, 1.5, 1.5, 1, 1, 1])
            cols[0].write(u.get("full_name", ""))
            cols[1].write(u.get("username", ""))
            cols[2].write(u.get("email", ""))
            cols[3].write(u.get("role", ""))
            cols[4].write("Active" if is_active else "Inactive")

            if cols[5].button("Edit", key=f"edit_btn_{sub}"):
                st.session_state[edit_key] = not st.session_state.get(edit_key, False)

            if cols[6].button("Delete", key=f"delete_btn_{sub}"):
                st.session_state[delete_key] = not st.session_state.get(delete_key, False)

            suspend_key = f"suspend_{sub}"
            next_active = not is_active
            btn_label = "Suspend" if is_active else "Resume"
            if cols[7].button(btn_label, key=f"suspend_btn_{sub}"):
                st.session_state[suspend_key] = True

            if st.session_state.get(suspend_key, False):
                st.warning(f"{btn_label} user **{u.get('username', '')}**?")
                c1, c2 = st.columns(2)
                if c1.button("Yes", key=f"confirm_suspend_{sub}"):
                    result = _update_user(sub, is_active=next_active)
                    if result is True:
                        st.success(f"User {btn_label.lower()}d!")
                        st.session_state[suspend_key] = False
                        st.rerun()
                    else:
                        st.error(f"Failed to {btn_label.lower()}: {result}")
                if c2.button("Cancel", key=f"cancel_suspend_{sub}"):
                    st.session_state[suspend_key] = False
                    st.rerun()

            if st.session_state.get(edit_key, False):
                with st.container(border=True):
                    st.markdown(f"**Editing:** {u.get('username', '')}")
                    with st.form(f"edit_form_{sub}"):
                        new_full_name = st.text_input("Full Name", value=u.get("full_name", ""))
                        new_username = st.text_input("Username", value=u.get("username", ""))
                        new_email = st.text_input("Email", value=u.get("email", ""))
                        new_password = st.text_input("New Password (leave blank to keep)", type="password")
                        new_role = st.selectbox(
                            "Role",
                            ALL_ROLES,
                            index=_role_index(u.get("role", "hospital_user")),
                        )
                        new_active = st.checkbox("Is Active", value=is_active)
                        col1, col2 = st.columns(2)
                        with col1:
                            save = st.form_submit_button("Save", use_container_width=True)
                        with col2:
                            cancel = st.form_submit_button("Cancel", use_container_width=True)

                    if save:
                        kwargs = {
                            "sub": sub,
                            "full_name": new_full_name,
                            "username": new_username,
                            "email": new_email,
                            "role": new_role,
                            "is_active": new_active,
                        }
                        if new_password:
                            kwargs["password"] = new_password
                        result = _update_user(**kwargs)
                        if result is True:
                            st.success("User updated!")
                            st.session_state[edit_key] = False
                            st.rerun()
                        else:
                            st.error(f"Failed to update user: {result}")
                    if cancel:
                        st.session_state[edit_key] = False
                        st.rerun()

            if st.session_state.get(delete_key, False):
                st.warning(f"Delete user **{u.get('username', '')}**?")
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Yes, delete", key=f"confirm_del_{sub}"):
                        if _delete_user(sub):
                            st.success("User deleted!")
                            st.session_state[delete_key] = False
                            st.rerun()
                        else:
                            st.error("Failed to delete user")
                with col2:
                    if st.button("Cancel", key=f"cancel_del_{sub}"):
                        st.session_state[delete_key] = False
                        st.rerun()
            st.divider()
    else:
        st.info("No users found in this hospital")

    st.markdown("### Create New User")
    with st.form("create_user_form"):
        new_full_name = st.text_input("Full Name")
        new_username = st.text_input("Username")
        new_email = st.text_input("Email")
        new_password = st.text_input("Password", type="password")
        new_role = st.selectbox("Role", ALL_ROLES)
        col1, col2 = st.columns([1, 3])
        with col1:
            create_clicked = st.form_submit_button("Create User", use_container_width=True)

    if create_clicked:
        if not all([new_username, new_email, new_password]):
            st.error("All fields are required")
        elif len(new_password) < 8:
            st.error("Password must be at least 8 characters")
        else:
            result = _create_user(new_username, new_password, new_email, new_role, new_full_name)
            if isinstance(result, dict):
                st.success(f"User '{new_username}' created successfully!")
                st.rerun()
            else:
                st.error(f"Failed to create user: {result}")


# ---------------------------------------------------------------------------
# UI: Tenant management (with subscription lifecycle)
# ---------------------------------------------------------------------------


def _tenant_action_button(action: str, tenant_id: str, **kwargs: Any):
    """Execute a tenant lifecycle action and return True on success."""
    action_map = {
        "activate": _activate_tenant,
        "reactivate": _reactivate_tenant,
        "suspend": lambda tid: _suspend_tenant(tid, kwargs.get("reason", "Manual suspension")),
        "subscribe": lambda tid: _subscribe_tenant(
            tid,
            kwargs["plan"],
            billing_cycle=kwargs.get("billing_cycle", "monthly"),
            start_trial=kwargs.get("start_trial", False),
        ),
        "upgrade": lambda tid: _upgrade_tenant(
            tid,
            kwargs["plan"],
            billing_cycle=kwargs.get("billing_cycle"),
        ),
        "downgrade": lambda tid: _downgrade_tenant(
            tid,
            kwargs["plan"],
            billing_cycle=kwargs.get("billing_cycle"),
            effective_at_end=kwargs.get("effective_at_end", False),
        ),
        "renew": lambda tid: _renew_tenant(tid, billing_cycle=kwargs.get("billing_cycle")),
    }
    fn = action_map.get(action)
    if not fn:
        st.error(f"Unknown action: {action}")
        return False
    result = fn(tenant_id)
    if isinstance(result, dict):
        st.success(f"Action '{action}' completed for {tenant_id}")
        return True
    st.error(f"Action '{action}' failed: {result}")
    return False


def _format_dt(value) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, str):
        return value
    return value.isoformat()


def _render_subscription_state(tenant_id: str):
    state = _get_subscription_state(tenant_id)
    if not state:
        st.info("No subscription state available")
        return

    sub = state.get("subscription", {})
    susp = state.get("suspension", {})

    col1, col2, col3 = st.columns(3)
    col1.metric("Status", state.get("status", "N/A"))
    col2.metric("Active", "Yes" if state.get("is_active") else "No")
    col3.metric("Plan", sub.get("display_name", sub.get("plan", "N/A")))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Billing Cycle", sub.get("billing_cycle", "N/A") or "N/A")
    c2.metric("Start", _format_dt(sub.get("start"))[:10])
    c3.metric("End", _format_dt(sub.get("end"))[:10])
    c4.metric("Auto Renew", "Yes" if sub.get("auto_renew") else "No")

    st.caption(
        f"Expired: {sub.get('is_expired', False)} | In grace: {sub.get('in_grace_period', False)} | "
        f"Has used trial: {sub.get('has_used_trial', False)}"
    )

    if susp.get("suspended_at"):
        st.warning(f"Suspended at: {_format_dt(susp['suspended_at'])} | Reason: {susp.get('reason', 'N/A')}")
    if susp.get("reactivated_at"):
        st.info(f"Reactivated at: {_format_dt(susp['reactivated_at'])}")


def _render_subscription_history(tenant_id: str):
    subs = _get_tenant_subscriptions(tenant_id)
    if not subs:
        st.info("No subscription history")
        return
    for s in subs:
        with st.container(border=True):
            cols = st.columns([2, 2, 2, 2, 2, 1])
            cols[0].write(str(s.get("subscription_id", ""))[:8])
            cols[1].write(s.get("status", "N/A"))
            cols[2].write(s.get("billing_cycle", "N/A"))
            cols[3].write(str(s.get("start_date", "N/A")))
            cols[4].write(str(s.get("end_date", "N/A")))
            cols[5].write("Yes" if s.get("auto_renew") else "No")


def _render_subscription_audit_log(tenant_id: str):
    logs = _get_subscription_audit_log(tenant_id)
    if not logs:
        st.info("No audit log entries")
        return
    for log in logs:
        with st.container(border=True):
            cols = st.columns([3, 2, 3, 3])
            cols[0].write(log.get("event_type", "N/A"))
            cols[1].write(log.get("actor_type", "N/A"))
            cols[2].write(log.get("reason") or "-")
            cols[3].write(_format_dt(log.get("created_at")))


def _show_tenants_page():
    me = st.session_state.get("me", {})
    is_super = me.get("is_super_admin", False)
    if not is_super:
        st.warning("You do not have permission to manage tenants.")
        return

    st.subheader("Tenant Management")

    catalog = _get_plan_catalog()
    plan_slugs = [p["plan"] for p in catalog] or ["free_trial", "basic", "standard", "premium", "enterprise"]
    plan_display = {p["plan"]: f"{p['display_name']} (${p['monthly_price']}/mo)" for p in catalog}

    tenants = _get_tenants()
    if tenants:
        for t in tenants:
            tid = t.get("tenant_id", "")
            is_active = t.get("is_active", False)
            status = t.get("status", "active")
            current_plan = t.get("subscription_plan", "")

            is_terminated = status == "terminated"
            cols = st.columns([2.5, 3, 1.2, 1.5, 1.2, 1, 1, 1])
            cols[0].write(tid)
            cols[1].write(t.get("name", ""))
            cols[2].write(status)
            cols[3].write(t.get("subscription_plan", ""))
            cols[4].write(t.get("subscription_billing_cycle") or "-")
            cols[5].write("Yes" if is_active else "No")

            with cols[6]:
                if is_terminated:
                    st.button("Terminated", key=f"terminated_{tid}", disabled=True)
                elif is_active:
                    if st.button("Suspend", key=f"suspend_{tid}"):
                        st.session_state[f"suspend_dialog_{tid}"] = True
                else:
                    if st.button("Activate", key=f"activate_{tid}"):
                        if _tenant_action_button("activate", tid):
                            st.rerun()

            with cols[7]:
                if not is_terminated:
                    if st.button("Terminate", key=f"terminate_{tid}"):
                        st.session_state[f"terminate_dialog_{tid}"] = True

            # Inline suspend reason dialog
            if st.session_state.get(f"suspend_dialog_{tid}", False):
                with st.container(border=True):
                    reason = st.text_input("Suspension reason", key=f"suspend_reason_{tid}")
                    c1, c2 = st.columns(2)
                    if c1.button("Confirm Suspend", key=f"suspend_confirm_{tid}"):
                        if reason and len(reason) >= 5:
                            if _tenant_action_button("suspend", tid, reason=reason):
                                st.session_state[f"suspend_dialog_{tid}"] = False
                                st.rerun()
                        else:
                            st.error("Reason must be at least 5 characters")
                    if c2.button("Cancel", key=f"suspend_cancel_{tid}"):
                        st.session_state[f"suspend_dialog_{tid}"] = False
                        st.rerun()

            # Inline terminate reason dialog
            if st.session_state.get(f"terminate_dialog_{tid}", False):
                with st.container(border=True):
                    st.error(f"Terminating tenant **{tid}** is irreversible.")
                    reason = st.text_input("Termination reason", key=f"terminate_reason_{tid}")
                    c1, c2 = st.columns(2)
                    if c1.button("Confirm Terminate", key=f"terminate_confirm_{tid}"):
                        if reason and len(reason) >= 5:
                            result = _terminate_tenant(tid, reason)
                            if isinstance(result, dict):
                                st.success(f"Tenant {tid} terminated")
                                st.session_state[f"terminate_dialog_{tid}"] = False
                                st.rerun()
                            else:
                                st.error(f"Termination failed: {result}")
                        else:
                            st.error("Reason must be at least 5 characters")
                    if c2.button("Cancel", key=f"terminate_cancel_{tid}"):
                        st.session_state[f"terminate_dialog_{tid}"] = False
                        st.rerun()

            # Expanders for details / actions
            with st.expander(f"Details & actions for {t.get('name', tid)}", expanded=False):
                tab_state, tab_history, tab_audit, tab_edit, tab_sub, tab_usage = st.tabs(
                    ["State", "History", "Audit Log", "Edit", "Subscription", "Usage"]
                )

                with tab_state:
                    _render_subscription_state(tid)

                with tab_history:
                    _render_subscription_history(tid)

                with tab_audit:
                    _render_subscription_audit_log(tid)

                with tab_edit:
                    with st.form(f"tenant_edit_form_{tid}"):
                        st.markdown("**Basic Details**")
                        edit_name = st.text_input("Hospital Name", value=t.get("name", ""), key=f"edit_name_{tid}")
                        edit_country = st.text_input("Country", value=t.get("country") or "", key=f"edit_country_{tid}")
                        edit_city = st.text_input("City", value=t.get("city") or "", key=f"edit_city_{tid}")
                        edit_address = st.text_area("Address", value=t.get("address") or "", key=f"edit_address_{tid}")

                        st.markdown("**Contact**")
                        edit_contact_name = st.text_input(
                            "Primary Contact Name",
                            value=t.get("primary_contact_name") or "",
                            key=f"edit_contact_name_{tid}",
                        )
                        edit_contact_email = st.text_input(
                            "Primary Contact Email",
                            value=t.get("primary_contact_email") or "",
                            key=f"edit_contact_email_{tid}",
                        )
                        edit_contact_phone = st.text_input(
                            "Primary Contact Phone",
                            value=t.get("primary_contact_phone") or "",
                            key=f"edit_contact_phone_{tid}",
                        )
                        edit_billing_email = st.text_input(
                            "Billing Email",
                            value=t.get("billing_email") or "",
                            key=f"edit_billing_email_{tid}",
                        )

                        st.markdown("**Regional / Branding**")
                        edit_timezone = st.text_input(
                            "Timezone", value=t.get("timezone") or "UTC", key=f"edit_tz_{tid}"
                        )
                        edit_currency = st.text_input(
                            "Currency", value=t.get("currency") or "USD", key=f"edit_currency_{tid}"
                        )
                        edit_date_format = st.text_input(
                            "Date Format", value=t.get("date_format") or "%Y-%m-%d", key=f"edit_datefmt_{tid}"
                        )
                        edit_logo_url = st.text_input(
                            "Logo URL", value=t.get("logo_url") or "", key=f"edit_logo_{tid}"
                        )
                        edit_data_region = st.text_input(
                            "Data Region", value=t.get("data_region") or "", key=f"edit_region_{tid}"
                        )

                        edit_status = st.selectbox(
                            "Status",
                            ["trial", "active", "suspended", "terminated"],
                            index=["trial", "active", "suspended", "terminated"].index(status)
                            if status in ["trial", "active", "suspended", "terminated"]
                            else 1,
                            key=f"edit_status_{tid}",
                        )

                        save = st.form_submit_button("Save Changes", use_container_width=True)

                    if save:
                        result = _update_tenant(
                            tid,
                            name=edit_name or None,
                            country=edit_country or None,
                            city=edit_city or None,
                            address=edit_address or None,
                            primary_contact_name=edit_contact_name or None,
                            primary_contact_email=edit_contact_email or None,
                            primary_contact_phone=edit_contact_phone or None,
                            billing_email=edit_billing_email or None,
                            timezone=edit_timezone or None,
                            currency=edit_currency or None,
                            date_format=edit_date_format or None,
                            logo_url=edit_logo_url or None,
                            data_region=edit_data_region or None,
                            status=edit_status,
                        )
                        if result is True:
                            st.success("Tenant updated!")
                            st.rerun()
                        else:
                            st.error(f"Failed to update tenant: {result}")

                with tab_sub:
                    sub_col1, sub_col2 = st.columns(2)
                    new_plan = sub_col1.selectbox(
                        "Plan",
                        plan_slugs,
                        index=plan_slugs.index(current_plan) if current_plan in plan_slugs else 0,
                        format_func=lambda x: plan_display.get(x, x),
                        key=f"sub_plan_{tid}",
                    )
                    new_cycle = sub_col2.selectbox(
                        "Billing Cycle",
                        ["monthly", "annual"],
                        index=0 if t.get("subscription_billing_cycle") != "annual" else 1,
                        key=f"sub_cycle_{tid}",
                    )
                    c1, c2, c3, c4 = st.columns(4)
                    if c1.button("Subscribe", key=f"btn_subscribe_{tid}"):
                        if _tenant_action_button(
                            "subscribe", tid, plan=new_plan, billing_cycle=new_cycle
                        ):
                            st.rerun()
                    if c2.button("Upgrade", key=f"btn_upgrade_{tid}"):
                        if _tenant_action_button("upgrade", tid, plan=new_plan, billing_cycle=new_cycle):
                            st.rerun()
                    if c3.button("Downgrade", key=f"btn_downgrade_{tid}"):
                        if _tenant_action_button(
                            "downgrade", tid, plan=new_plan, billing_cycle=new_cycle, effective_at_end=False
                        ):
                            st.rerun()
                    if c4.button("Renew", key=f"btn_renew_{tid}"):
                        if _tenant_action_button("renew", tid, billing_cycle=new_cycle):
                            st.rerun()

                    if not is_active and status == "suspended":
                        if st.button("Reactivate", key=f"btn_reactivate_{tid}"):
                            if _tenant_action_button("reactivate", tid):
                                st.rerun()

                with tab_usage:
                    stats = _get_tenant_stats(tid)
                    if stats:
                        st.markdown("### Usage Statistics")

                        # Users
                        st.markdown("**Users**")
                        uc1, uc2, uc3, uc4 = st.columns(4)
                        uc1.metric("Local DB", stats.get("user_count", 0))
                        uc2.metric("Active (Local)", stats.get("active_user_count", 0))
                        uc3.metric("Keycloak Total", stats.get("kc_user_count", 0))
                        uc4.metric("Keycloak Active", stats.get("kc_active_user_count", 0))

                        # Patients & Activity
                        st.markdown("**Patients & Activity**")
                        pc1, pc2, pc3, pc4 = st.columns(4)
                        pc1.metric("Total Patients", stats.get("patient_count", 0))
                        pc2.metric("New This Month", stats.get("patients_this_month", 0))
                        pc3.metric("Visits", stats.get("visit_count", 0))
                        pc4.metric("Appointments", stats.get("appointment_count", 0))

                        # Storage & API
                        st.markdown("**Infrastructure**")
                        sc1, sc2, sc3 = st.columns(3)
                        db_mb = stats.get("db_size_mb", 0)
                        sc1.metric("Database Size", f"{db_mb} MB" if db_mb else "N/A")
                        sc2.metric("API Calls This Month", stats.get("api_calls_this_month", 0))
                        usage_pct = stats.get("usage_pct")
                        if usage_pct is not None:
                            sc3.metric("Usage %", f"{usage_pct}%")
                        else:
                            sc3.metric("Max Users", stats.get("max_users", "N/A") or "N/A")
                    else:
                        st.info("No usage statistics available.")

            st.divider()
    else:
        st.info("No tenants found")

    with st.expander("Create New Tenant", expanded=False):
        st.info("Only essential fields are required. The tenant admin will be forced to change the temporary password on first login and can complete the remaining profile information afterwards.")
        with st.form("create_tenant_form"):
            hospital_name = st.text_input("Hospital Name *")
            admin_full_name = st.text_input("Admin Full Name")
            admin_username = st.text_input("Admin Username *")
            admin_email = st.text_input("Admin Email *")
            admin_password = st.text_input("Temporary Admin Password *", type="password")

            col_submit, _ = st.columns([1, 3])
            with col_submit:
                submitted = st.form_submit_button("Create Tenant", use_container_width=True)

        if submitted:
            if not all([hospital_name, admin_username, admin_email, admin_password]):
                st.error("All required fields must be filled")
            elif len(admin_password) < 8:
                st.error("Password must be at least 8 characters")
            else:
                result = _create_tenant(
                    hospital_name,
                    admin_username,
                    admin_password,
                    admin_email,
                    "free_trial",
                    admin_full_name=admin_full_name,
                    billing_cycle="monthly",
                )
                if isinstance(result, dict):
                    st.success(f"Tenant '{hospital_name}' created! ID: {result['tenant_id']}")
                    st.rerun()
                else:
                    st.error(f"Failed to create tenant: {result}")


# ---------------------------------------------------------------------------
# UI: Subscription plan catalog
# ---------------------------------------------------------------------------


def _show_plans_page():
    me = st.session_state.get("me", {})
    if not me.get("is_super_admin", False):
        st.warning("You do not have permission to view subscription plans.")
        return

    st.subheader("Subscription Plan Catalog")

    canonical = _get_plan_catalog()
    db_plans = _get_subscription_plans()

    if canonical:
        st.markdown("### Canonical Plans")
        for p in canonical:
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([2, 2, 2, 3])
                c1.write(f"**{p.get('display_name')}**")
                c2.write(f"Slug: `{p.get('plan')}`")
                c3.write(f"Rank: {p.get('rank')}")
                c4.write(f"Monthly: ${p.get('monthly_price')} | Annual: ${p.get('annual_price')}")
                st.caption(
                    f"Trial days: {p.get('trial_days')} | Max users: {p.get('max_users')} | "
                    f"Features: {', '.join(p.get('features', []))}"
                )
    else:
        st.info("No canonical plans available")

    if db_plans:
        st.markdown("### Database-backed Plans")
        for p in db_plans:
            pid = str(p.get("plan_id", ""))
            edit_key = f"plan_edit_{pid}"
            delete_key = f"plan_delete_{pid}"

            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([2, 2, 2, 3])
                c1.write(f"**{p.get('plan_name')}**")
                c2.write(f"Monthly: ${p.get('monthly_price')}")
                c3.write(f"Annual: ${p.get('annual_price')}")
                c4.write(f"Active: {'Yes' if p.get('is_active') else 'No'}")
                st.caption(
                    f"Max users: {p.get('max_users')} | Description: {p.get('description', 'N/A')}"
                )

                c1, c2 = st.columns([1, 6])
                if c1.button("Edit", key=f"plan_edit_btn_{pid}"):
                    st.session_state[edit_key] = not st.session_state.get(edit_key, False)
                if c2.button("Delete", key=f"plan_del_btn_{pid}"):
                    st.session_state[delete_key] = not st.session_state.get(delete_key, False)

                if st.session_state.get(delete_key, False):
                    with st.container(border=True):
                        st.warning("Are you sure you want to delete this plan?")
                        c1, c2 = st.columns(2)
                        if c1.button("Yes, Delete", key=f"plan_del_confirm_{pid}"):
                            if _delete_plan(pid):
                                st.success("Plan deleted")
                                st.rerun()
                            else:
                                st.error("Failed to delete plan")
                        if c2.button("Cancel", key=f"plan_del_cancel_{pid}"):
                            st.session_state[delete_key] = False
                            st.rerun()

                if st.session_state.get(edit_key, False):
                    with st.container(border=True):
                        st.markdown("**Edit Plan**")
                        with st.form(f"plan_edit_form_{pid}"):
                            new_name = st.text_input("Plan Name", value=p.get("plan_name", ""))
                            new_desc = st.text_input("Description", value=p.get("description", "") or "")
                            new_monthly = st.number_input("Monthly Price", value=float(p.get("monthly_price", 0)), min_value=0.0)
                            new_annual = st.number_input("Annual Price", value=float(p.get("annual_price", 0)), min_value=0.0)
                            new_max_users = st.number_input("Max Users", value=p.get("max_users") or 0, min_value=0, step=1)
                            new_active = st.checkbox("Is Active", value=p.get("is_active", True))

                            save = st.form_submit_button("Save", use_container_width=True)

                        if save:
                            result = _update_plan(
                                plan_id=pid,
                                plan_name=new_name,
                                description=new_desc or None,
                                monthly_price=new_monthly,
                                annual_price=new_annual,
                                max_users=new_max_users if new_max_users > 0 else None,
                                is_active=new_active,
                            )
                            if isinstance(result, dict):
                                st.success("Plan updated!")
                                st.rerun()
                            else:
                                st.error(f"Failed to update: {result}")
    else:
        st.info("No DB-backed plans available")

    with st.expander("Create New Plan", expanded=False):
        with st.form("create_plan_form"):
            plan_name = st.text_input("Plan Name *")
            description = st.text_input("Description")
            monthly_price = st.number_input("Monthly Price", min_value=0.0, value=0.0)
            annual_price = st.number_input("Annual Price", min_value=0.0, value=0.0)
            max_users = st.number_input("Max Users", min_value=0, value=0, step=1)
            is_active = st.checkbox("Is Active", value=True)

            submitted = st.form_submit_button("Create Plan", use_container_width=True)

        if submitted:
            if not plan_name:
                st.error("Plan name is required")
            else:
                result = _create_plan(
                    plan_name=plan_name,
                    description=description or None,
                    monthly_price=monthly_price,
                    annual_price=annual_price,
                    max_users=max_users if max_users > 0 else None,
                    is_active=is_active,
                )
                if isinstance(result, dict):
                    st.success(f"Plan '{plan_name}' created!")
                    st.rerun()
                else:
                    st.error(f"Failed to create plan: {result}")


# ---------------------------------------------------------------------------
# UI: Announcements
# ---------------------------------------------------------------------------


def _show_announcements_page():
    me = st.session_state.get("me", {})
    if not me.get("is_super_admin", False):
        st.warning("You do not have permission to manage announcements.")
        return

    st.subheader("Announcements")

    # Fetch tenants for the multiselect dropdown
    all_tenants = _get_tenants()
    tenant_map = {}
    if isinstance(all_tenants, list) and all_tenants:
        tenant_map = {t["tenant_id"]: t.get("name", t["tenant_id"]) for t in all_tenants}

    announcements = _get_announcements()
    if announcements:
        for a in announcements:
            aid = str(a.get("announcement_id", ""))
            edit_key = f"ann_edit_{aid}"
            delete_key = f"ann_delete_{aid}"

            with st.container(border=True):
                st.markdown(f"**{a.get('title')}**")
                st.write(a.get("body", ""))

                # Show target tenant names instead of IDs
                target_ids = a.get("target_tenant_ids") or []
                if a.get("audience") == "all":
                    target_display = "All tenants"
                elif target_ids:
                    names = [tenant_map.get(tid, tid) for tid in target_ids]
                    target_display = ", ".join(names)
                else:
                    target_display = "None"

                st.caption(
                    f"Audience: {a.get('audience')} | Targets: {target_display} | "
                    f"Publish: {_format_dt(a.get('publish_at'))} | Expires: {_format_dt(a.get('expires_at'))}"
                )

                c1, c2 = st.columns([1, 6])
                if c1.button("Edit", key=f"ann_edit_btn_{aid}"):
                    st.session_state[edit_key] = not st.session_state.get(edit_key, False)
                if c2.button("Delete", key=f"ann_del_btn_{aid}"):
                    st.session_state[delete_key] = not st.session_state.get(delete_key, False)

                if st.session_state.get(delete_key, False):
                    with st.container(border=True):
                        st.warning("Are you sure you want to delete this announcement?")
                        c1, c2 = st.columns(2)
                        if c1.button("Yes, Delete", key=f"ann_del_confirm_{aid}"):
                            if _delete_announcement(aid):
                                st.success("Announcement deleted")
                                st.rerun()
                            else:
                                st.error("Failed to delete announcement")
                        if c2.button("Cancel", key=f"ann_del_cancel_{aid}"):
                            st.session_state[delete_key] = False
                            st.rerun()

                if st.session_state.get(edit_key, False):
                    with st.container(border=True):
                        st.markdown("**Edit Announcement**")
                        with st.form(f"ann_edit_form_{aid}"):
                            new_title = st.text_input("Title", value=a.get("title", ""))
                            new_body = st.text_area("Body", value=a.get("body", ""))
                            new_audience = st.selectbox(
                                "Audience",
                                ["all", "selected"],
                                index=0 if a.get("audience") == "all" else 1,
                            )
                            # Multiselect with tenant names
                            preselected = a.get("target_tenant_ids") or []
                            new_targets = st.multiselect(
                                "Target Tenants",
                                options=list(tenant_map.keys()),
                                default=[tid for tid in preselected if tid in tenant_map],
                                format_func=lambda x: tenant_map.get(x, x),
                            )
                            new_publish = st.datetime_input(
                                "Publish at",
                                value=a.get("publish_at") or datetime.now(timezone.utc),
                            )
                            has_expiry = st.checkbox(
                                "Set expiry",
                                value=a.get("expires_at") is not None,
                            )
                            new_expires = st.datetime_input(
                                "Expires at",
                                value=a.get("expires_at") or datetime.now(timezone.utc),
                            ) if has_expiry else None

                            save = st.form_submit_button("Save", use_container_width=True)

                        if save:
                            target_ids = None if new_audience == "all" else new_targets
                            result = _update_announcement(
                                announcement_id=aid,
                                title=new_title,
                                body=new_body,
                                audience=new_audience,
                                target_tenant_ids=target_ids,
                                publish_at=new_publish,
                                expires_at=new_expires,
                            )
                            if isinstance(result, dict):
                                st.success("Announcement updated!")
                                st.rerun()
                            else:
                                st.error(f"Failed to update: {result}")
    else:
        st.info("No announcements")

    with st.expander("Create Announcement", expanded=False):
        with st.form("announcement_form"):
            title = st.text_input("Title *")
            body = st.text_area("Body *")
            audience = st.selectbox("Audience", ["all", "selected"])
            # Multiselect with tenant names
            target_tenants = st.multiselect(
                "Target Tenants (if audience=selected)",
                options=list(tenant_map.keys()),
                default=[],
                format_func=lambda x: tenant_map.get(x, x),
            )
            col1, col2 = st.columns(2)
            with col1:
                publish_now = st.checkbox("Publish immediately", value=True)
                publish_at = None if publish_now else st.datetime_input("Publish at", value=datetime.now(timezone.utc))
            with col2:
                has_expiry = st.checkbox("Set expiry")
                expires_at = st.datetime_input("Expires at", value=datetime.now(timezone.utc)) if has_expiry else None

            submitted = st.form_submit_button("Post Announcement", use_container_width=True)

        if submitted:
            if not title or not body:
                st.error("Title and body are required")
            else:
                target_ids = None if audience == "all" else target_tenants
                result = _create_announcement(
                    title=title,
                    body=body,
                    audience=audience,
                    target_tenant_ids=target_ids,
                    publish_at=publish_at,
                    expires_at=expires_at,
                )
                if isinstance(result, dict):
                    st.success("Announcement created!")
                    st.rerun()
                else:
                    st.error(f"Failed to create announcement: {result}")


# ---------------------------------------------------------------------------
# UI: System health dashboard (super admin)
# ---------------------------------------------------------------------------


def _show_health_page():
    me = st.session_state.get("me", {})
    if not me.get("is_super_admin", False):
        st.warning("You do not have permission to view system health.")
        return

    st.subheader("System Health Dashboard")

    health = _get_system_health()
    if not health:
        st.info("Unable to load system health.")
        return

    overall = health.get("overall", "unknown")
    healthy = health.get("healthy_count", 0)
    total = health.get("total_count", 0)

    # Overall status
    if overall == "healthy":
        st.success(f"All {total} services are healthy")
    elif overall == "degraded":
        st.warning(f"{healthy}/{total} services healthy — some services are degraded")
    else:
        st.error(f"{healthy}/{total} services healthy — system is unhealthy")

    st.markdown("### Service Status")
    services = health.get("services", {})
    if services:
        data = []
        for name, info in services.items():
            status = info.get("status", "unknown")
            error = info.get("error", "")
            data.append({
                "Service": name,
                "Status": status,
                "Error": error,
            })
        st.dataframe(data, use_container_width=True)

        # Visual grid
        cols = st.columns(4)
        for i, (name, info) in enumerate(services.items()):
            with cols[i % 4]:
                status = info.get("status", "unknown")
                if status == "ok":
                    st.success(name)
                elif status == "unreachable":
                    st.error(f"{name} (down)")
                else:
                    st.warning(f"{name} ({status})")
    else:
        st.info("No service data available")


# ---------------------------------------------------------------------------
# UI: Subscription self-service page (hospital admin)
# ---------------------------------------------------------------------------


def _show_subscription_page():
    me = st.session_state.get("me", {})
    tenant_id = me.get("tenant_id")

    st.subheader("Subscription")
    if not tenant_id:
        st.warning("No tenant associated with your account.")
        return

    state = _get_my_subscription()
    if not state:
        st.info("Unable to load subscription state.")
        return

    col1, col2, col3 = st.columns(3)
    col1.metric("Status", state.get("status", "N/A"))
    col2.metric("Active", "Yes" if state.get("is_active") else "No")
    col3.metric("Trial", "Yes" if state.get("is_trial") else "No")

    subscription = state.get("subscription") or {}
    if subscription:
        st.markdown("### Current Subscription")
        c1, c2, c3, c4 = st.columns(4)
        c1.write(f"**Plan:** {subscription.get('plan', 'N/A')}")
        c2.write(f"**Billing cycle:** {subscription.get('billing_cycle', 'N/A')}")
        c3.write(f"**Start:** {_format_dt(subscription.get('start') or subscription.get('start_date'))}")
        c4.write(f"**End:** {_format_dt(subscription.get('end') or subscription.get('end_date'))}")

    # Show free trial remaining days
    if state.get("is_trial"):
        trial_end = subscription.get("end") or subscription.get("end_date")
        if trial_end:
            from datetime import datetime
            try:
                end_dt = datetime.fromisoformat(trial_end.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                remaining_days = max(0, (end_dt - now).days)
                st.info(f"Free trial: {remaining_days} days remaining")
            except Exception:
                pass

    suspension = state.get("suspension") or {}
    if suspension.get("is_suspended"):
        st.error(
            f"**Account suspended** — reason: {suspension.get('reason', 'N/A')}"
        )
        st.caption(f"Suspended at: {_format_dt(suspension.get('suspended_at'))}")

    features = state.get("feature_gates") or {}
    if features:
        st.markdown("### Feature Gates")
        st.json(features)

    # Upgrade / Downgrade self-service
    st.markdown("---")
    st.markdown("### Change Plan")
    catalog = _get_plan_catalog()
    plan_slugs = [p["plan"] for p in catalog] if catalog else ["free_trial", "basic", "standard", "premium", "enterprise"]
    plan_display = {p["plan"]: f"{p['display_name']} (${p['monthly_price']}/mo)" for p in catalog}

    current_plan = subscription.get("plan", "free_trial")
    current_cycle = subscription.get("billing_cycle", "monthly")

    with st.form("change_plan_form"):
        new_plan = st.selectbox(
            "New Plan",
            plan_slugs,
            index=plan_slugs.index(current_plan) if current_plan in plan_slugs else 0,
            format_func=lambda x: plan_display.get(x, x),
        )
        new_cycle = st.selectbox(
            "Billing Cycle",
            ["monthly", "annual"],
            index=0 if current_cycle != "annual" else 1,
        )
        action = st.radio("Action", ["Upgrade", "Downgrade"], horizontal=True)
        submitted = st.form_submit_button("Submit", use_container_width=True)

    if submitted:
        if action == "Upgrade":
            result = _upgrade_my_subscription(new_plan, new_cycle)
        else:
            result = _downgrade_my_subscription(new_plan, new_cycle)
        if isinstance(result, dict):
            st.success(f"Plan {action.lower()}d to {new_plan} ({new_cycle})")
            st.rerun()
        else:
            st.error(f"Failed to {action.lower()}: {result}")


# ---------------------------------------------------------------------------
# UI: Tenant announcements page (hospital admin)
# ---------------------------------------------------------------------------


def _show_tenant_announcements_page():
    me = st.session_state.get("me", {})
    tenant_id = me.get("tenant_id")

    st.subheader("Announcements")
    if not tenant_id:
        st.warning("No tenant associated with your account.")
        return

    announcements = _get_my_announcements()
    if announcements:
        for a in announcements:
            with st.container(border=True):
                st.markdown(f"**{a.get('title')}**")
                st.write(a.get("body", ""))
                st.caption(
                    f"Published: {_format_dt(a.get('publish_at'))} | "
                    f"Expires: {_format_dt(a.get('expires_at'))}"
                )
    else:
        st.info("No announcements for your hospital.")


# ---------------------------------------------------------------------------
# UI: Dashboard shell
# ---------------------------------------------------------------------------


def _show_dashboard():
    me = st.session_state.get("me")
    if not me:
        me = _get_me()
        if me:
            st.session_state["me"] = me

    if not me:
        st.error("Session expired. Please login again.")
        _logout()
        return

    is_super = me.get("is_super_admin", False)
    role = me.get("role", "hospital_user")
    tenant_id = me.get("tenant_id") or "N/A"

    # Proactive lockout: if this is a tenant user, confirm the tenant is still active.
    if not is_super and tenant_id and tenant_id != "N/A":
        sub_state = _get_my_subscription()
        if st.session_state.get("tenant_lockout"):
            st.rerun()
        if sub_state and sub_state.get("status") in ("suspended", "terminated"):
            st.session_state["tenant_lockout"] = {
                "code": "TENANT_SUSPENDED" if sub_state.get("status") == "suspended" else "TENANT_TERMINATED",
                "message": f"Tenant account is {sub_state.get('status')}.",
            }
            st.rerun()

    # Determine navigation based on actual role
    if is_super:
        nav_options = ["Users", "Tenants", "Plans", "Announcements", "Health", "Profile"]
        role_display = "Super Admin"
    elif role == "hospital_admin":
        nav_options = ["Users", "Subscription", "Announcements", "Profile"]
        role_display = "Hospital Admin"
    else:
        nav_options = ["Profile"]
        role_display = role.replace("_", " ").title()

    with st.sidebar:
        st.title("Hospital Flow")
        st.markdown(f"**User:** {me.get('preferred_username', 'N/A')}")
        st.markdown(f"**Tenant:** {tenant_id}")
        st.markdown(f"**Role:** {role_display}")
        st.markdown("---")
        nav = st.radio("Navigation", nav_options, index=0)
        st.markdown("---")
        if st.button("Logout", use_container_width=True):
            _logout()

    if nav == "Profile":
        st.subheader("Your Profile")
        st.json(me)
        return

    if nav == "Tenants":
        _show_tenants_page()
        return

    if nav == "Plans":
        _show_plans_page()
        return

    if nav == "Announcements":
        if is_super:
            _show_announcements_page()
        else:
            _show_tenant_announcements_page()
        return

    if nav == "Subscription":
        _show_subscription_page()
        return

    if nav == "Health":
        _show_health_page()
        return

    _show_users_page()


def _show_lockout():
    lockout = st.session_state.get("tenant_lockout", {})
    code = lockout.get("code", "TENANT_SUSPENDED")
    message = lockout.get("message", "Your tenant account is locked.")

    st.title("Account Locked")
    if code == "TENANT_TERMINATED":
        st.error("This hospital account has been terminated. Contact support for assistance.")
    else:
        st.error("This hospital account is suspended. Contact support for assistance.")
    st.caption(f"Reason: {message}")
    if st.button("Logout", use_container_width=True):
        _logout()


def main():
    if st.session_state.get("tenant_lockout"):
        _show_lockout()
        return

    page = st.session_state.get("page", "login")
    access_token = st.session_state.get("access_token")

    if access_token:
        me = _get_me()
        if me:
            st.session_state["me"] = me
            _show_dashboard()
        else:
            if _try_refresh():
                st.rerun()
            else:
                _logout()
    elif page == "signup":
        _show_signup()
    else:
        st.session_state["page"] = "login"
        _show_login()


if __name__ == "__main__":
    main()
