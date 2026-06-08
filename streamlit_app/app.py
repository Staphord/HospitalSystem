from typing import Any

import httpx
import streamlit as st

API_BASE = "http://localhost:8000/api/v1"

st.set_page_config(page_title="Hospital Flow", layout="wide")


def _client() -> httpx.Client:
    headers = {}
    token = st.session_state.get("access_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=API_BASE, headers=headers, timeout=15.0)


def _try_refresh():
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


def _login(username: str, password: str) -> dict | str:
    r = _client().post("/auth/login", json={"username": username, "password": password})
    if r.status_code == 200:
        return r.json()
    try:
        body = r.json()
        return body.get("detail", f"Login failed ({r.status_code})")
    except Exception:
        return f"Login failed ({r.status_code})"


def _login_superadmin(username: str, password: str) -> dict | str:
    r = _client().post("/auth/superadmin/login", json={"username": username, "password": password})
    if r.status_code == 200:
        return r.json()
    try:
        body = r.json()
        return body.get("detail", f"Superadmin login failed ({r.status_code})")
    except Exception:
        return f"Superadmin login failed ({r.status_code})"


def _signup(hospital_name: str, admin_username: str, admin_password: str, admin_email: str, admin_full_name: str = "") -> dict | str:
    r = _client().post("/auth/signup", json={
        "hospital_name": hospital_name,
        "admin_username": admin_username,
        "admin_password": admin_password,
        "admin_email": admin_email,
        "admin_full_name": admin_full_name,
    })
    if r.status_code == 201:
        return r.json()
    try:
        body = r.json()
        detail = body.get("detail", r.text) if isinstance(body, dict) else r.text
    except Exception:
        detail = r.text if r.text else f"HTTP {r.status_code}"
    return detail


def _get_me() -> dict | None:
    r = _client().get("/me")
    if r.status_code == 401:
        if _try_refresh():
            r = _client().get("/me")
            if r.status_code == 200:
                return r.json()
        return None
    if r.status_code == 200:
        return r.json()
    return None


def _get_users() -> list[dict]:
    r = _client().get("/admin/users")
    if r.status_code == 200:
        return r.json()
    return []


def _create_user(username: str, password: str, email: str, role: str, full_name: str = "") -> dict | str:
    r = _client().post("/admin/users", json={
        "username": username,
        "password": password,
        "email": email,
        "full_name": full_name,
        "role": role,
    })
    if r.status_code == 201:
        return r.json()
    try:
        body = r.json()
        detail = body.get("detail", r.text) if isinstance(body, dict) else r.text
    except Exception:
        detail = r.text if r.text else f"HTTP {r.status_code}"
    return detail


def _update_user(sub: str, username: str | None = None, full_name: str | None = None, password: str | None = None, email: str | None = None, role: str | None = None) -> bool | str:
    payload = {}
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
    r = _client().patch(f"/admin/users/{sub}", json=payload)
    if r.status_code == 200:
        return True
    try:
        body = r.json()
        detail = body.get("detail", r.text) if isinstance(body, dict) else r.text
    except Exception:
        detail = r.text if r.text else f"HTTP {r.status_code}"
    return detail


def _delete_user(sub: str) -> bool:
    r = _client().delete(f"/admin/users/{sub}")
    return r.status_code == 204


def _get_all_users() -> list[dict]:
    r = _client().get("/superadmin/users")
    if r.status_code == 200:
        return r.json()
    return []


SA_ROLES = ["super_admin", "billing_manager", "support"]


def _create_superuser(
    username: str,
    password: str,
    email: str,
    full_name: str = "",
    role: str = "super_admin",
    mfa_secret: str = "",
) -> dict | str:
    r = _client().post("/superadmin/users", json={
        "username": username,
        "password": password,
        "email": email,
        "full_name": full_name or username,
        "role": role,
        "mfa_secret": mfa_secret or None,
    })
    if r.status_code == 201:
        return r.json()
    try:
        body = r.json()
        detail = body.get("detail", r.text) if isinstance(body, dict) else r.text
    except Exception:
        detail = r.text if r.text else f"HTTP {r.status_code}"
    return detail


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
    try:
        body = r.json()
        detail = body.get("detail", r.text) if isinstance(body, dict) else r.text
    except Exception:
        detail = r.text if r.text else f"HTTP {r.status_code}"
    return detail


def _delete_superuser(username: str) -> bool:
    r = _client().delete("/superadmin/users", json={"username": username})
    return r.status_code == 204


def _get_tenants() -> list[dict]:
    r = _client().get("/superadmin/tenants")
    if r.status_code == 200:
        return r.json()
    return []


def _create_tenant(hospital_name: str, admin_username: str, admin_password: str, admin_email: str, plan: str, admin_full_name: str = "") -> dict | str:
    r = _client().post("/superadmin/tenants", json={
        "hospital_name": hospital_name,
        "admin_full_name": admin_full_name,
        "admin_username": admin_username,
        "admin_password": admin_password,
        "admin_email": admin_email,
        "subscription_plan": plan,
    })
    if r.status_code == 201:
        return r.json()
    detail = r.json().get("detail", r.text) if r.text else f"HTTP {r.status_code}"
    return detail


def _update_tenant(tenant_id: str, is_active: bool | None = None, status: str | None = None) -> bool:
    payload = {}
    if is_active is not None:
        payload["is_active"] = is_active
    if status is not None:
        payload["status"] = status
    r = _client().patch(f"/superadmin/tenants/{tenant_id}", json=payload)
    return r.status_code == 200


def _logout():
    refresh_token = st.session_state.get("refresh_token")
    if refresh_token:
        try:
            _client().post("/auth/logout", json={"refresh_token": refresh_token})
        except Exception:
            pass
    for key in ["access_token", "refresh_token", "username", "session_id", "tenant_id", "me"]:
        st.session_state.pop(key, None)
    st.rerun()


def _show_login():
    st.title("Hospital Flow")
    st.subheader("Sign In")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        col1, col2 = st.columns(2)
        with col1:
            submitted = st.form_submit_button("Sign In", use_container_width=True)
        with col2:
            st.form_submit_button("Sign Up", use_container_width=True, on_click=lambda: st.session_state.update(page="signup"))

    if submitted:
        if not username or not password:
            st.error("Please enter username and password")
            return
        result = _login(username, password)
        if isinstance(result, str):
            # Regular login returned an error string; try superadmin
            sa_result = _login_superadmin(username, password)
            if isinstance(sa_result, dict):
                result = sa_result
            else:
                st.error(result)
                return
        if isinstance(result, dict):
            st.session_state["access_token"] = result["access_token"]
            st.session_state["refresh_token"] = result.get("refresh_token", "")
            st.session_state["username"] = username
            st.session_state["session_id"] = result.get("session_id", "")
            st.session_state["tenant_id"] = result.get("tenant_id")
            st.rerun()
        else:
            st.error("Invalid credentials")

    st.markdown("---")
    st.markdown("Don't have an account?")
    if st.button("Register a new hospital"):
        st.session_state["page"] = "signup"
        st.rerun()


def _show_signup():
    st.title("Hospital Flow")
    st.subheader("Register Your Hospital")

    with st.form("signup_form"):
        hospital_name = st.text_input("Hospital Name")
        admin_full_name = st.text_input("Admin Full Name")
        admin_username = st.text_input("Admin Username")
        admin_email = st.text_input("Admin Email")
        admin_password = st.text_input("Admin Password", type="password")
        admin_password_confirm = st.text_input("Confirm Password", type="password")

        col1, col2 = st.columns(2)
        with col1:
            submitted = st.form_submit_button("Register", use_container_width=True)
        with col2:
            st.form_submit_button("Back to Login", use_container_width=True, on_click=lambda: st.session_state.update(page="login"))

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

        result = _signup(hospital_name, admin_username, admin_password, admin_email, admin_full_name)
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

    if st.button("Back to Login"):
        st.session_state["page"] = "login"
        st.rerun()


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

        if users:
            st.markdown("#### All Fields: super_admin_id | full_name | username | email | role | is_active | last_login | created")
            for u in users:
                sid = u.get("super_admin_id", "")
                edit_key = f"sa_edit_{sid}"
                delete_key = f"sa_delete_{sid}"

                cols = st.columns([2, 2, 2, 2, 1.5, 1, 1, 1])
                cols[0].write(u.get("super_admin_id", "")[:8] + "...")
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
                            new_role = st.selectbox("Role", SA_ROLES, index=SA_ROLES.index(u.get("role", "super_admin")) if u.get("role") in SA_ROLES else 0)
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

    if users:
        st.markdown("### Users")
        for i, u in enumerate(users):
            sub = u.get("keycloak_sub", "")
            edit_key = f"edit_{sub}"
            delete_key = f"delete_{sub}"

            cols = st.columns([3, 3, 3, 2, 2, 1, 1])
            cols[0].write(u.get("full_name", ""))
            cols[1].write(u.get("username", ""))
            cols[2].write(u.get("email", ""))
            cols[3].write(u.get("role", ""))
            cols[4].write(u.get("hospital_id", ""))

            if cols[5].button("Edit", key=f"edit_btn_{sub}"):
                st.session_state[edit_key] = not st.session_state.get(edit_key, False)

            if cols[6].button("Delete", key=f"delete_btn_{sub}"):
                st.session_state[delete_key] = not st.session_state.get(delete_key, False)

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
                        col1, col2 = st.columns(2)
                        with col1:
                            save = st.form_submit_button("Save", use_container_width=True)
                        with col2:
                            cancel = st.form_submit_button("Cancel", use_container_width=True)

                    if save:
                        kwargs = {"sub": sub, "full_name": new_full_name, "username": new_username, "email": new_email, "role": new_role}
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


def _show_tenants_page():
    me = st.session_state.get("me", {})
    is_super = me.get("is_super_admin", False)
    if not is_super:
        st.warning("You do not have permission to manage tenants.")
        return

    st.subheader("Tenant Management")

    tenants = _get_tenants()
    if tenants:
        for t in tenants:
            tid = t.get("tenant_id", "")
            active_key = f"tenant_active_{tid}"
            is_active = t.get("is_active", False)
            status = t.get("status", "active")

            cols = st.columns([2, 3, 1, 1, 1, 1])
            cols[0].write(tid)
            cols[1].write(t.get("name", ""))
            cols[2].write(status)
            cols[3].write(t.get("subscription_plan", ""))
            cols[4].write("Yes" if is_active else "No")

            if is_active:
                if cols[5].button("Suspend", key=f"suspend_{tid}"):
                    if _update_tenant(tid, is_active=False, status="suspended"):
                        st.success(f"Tenant '{tid}' suspended")
                        st.rerun()
                    else:
                        st.error("Failed to suspend tenant")
            else:
                if cols[5].button("Activate", key=f"activate_{tid}"):
                    if _update_tenant(tid, is_active=True, status="active"):
                        st.success(f"Tenant '{tid}' activated")
                        st.rerun()
                    else:
                        st.error("Failed to activate tenant")
            st.divider()
    else:
        st.info("No tenants found")

    with st.expander("Create New Tenant", expanded=False):
        with st.form("create_tenant_form"):
            hospital_name = st.text_input("Hospital Name")
            admin_full_name = st.text_input("Admin Full Name")
            admin_username = st.text_input("Admin Username")
            admin_email = st.text_input("Admin Email")
            admin_password = st.text_input("Admin Password", type="password")
            admin_confirm = st.text_input("Confirm Password", type="password")
            plan = st.selectbox("Subscription Plan", ["standard", "premium", "enterprise"])
            col1, col2 = st.columns([1, 3])
            with col1:
                submitted = st.form_submit_button("Create Tenant", use_container_width=True)

        if submitted:
            if not all([hospital_name, admin_username, admin_email, admin_password]):
                st.error("All fields are required")
            elif admin_password != admin_confirm:
                st.error("Passwords do not match")
            elif len(admin_password) < 8:
                st.error("Password must be at least 8 characters")
            else:
                result = _create_tenant(hospital_name, admin_username, admin_password, admin_email, plan, admin_full_name)
                if isinstance(result, dict):
                    st.success(f"Tenant '{hospital_name}' created! ID: {result['tenant_id']}")
                    st.rerun()
                else:
                    st.error(f"Failed to create tenant: {result}")


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

    # Determine navigation based on actual role
    if is_super:
        nav_options = ["Users", "Tenants", "Profile"]
        role_display = "Super Admin"
    elif role == "hospital_admin":
        nav_options = ["Users", "Profile"]
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

    _show_users_page()


def main():
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