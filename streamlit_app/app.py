import httpx
import streamlit as st

API_BASE = "http://localhost:8000/api/v1"

st.set_page_config(page_title="Hospital Flow Login", layout="centered")

st.title("Hospital Flow")
st.caption("Multi-tenant hospital management system")


def _show_login():
    st.subheader("Sign In")

    with st.form("login_form"):
        username = st.text_input("Username", placeholder="username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", type="primary", use_container_width=True)

    if submitted:
        if not username or not password:
            st.error("Username and password are required")
            return

        with st.spinner("Authenticating..."):
            try:
                resp = httpx.post(
                    f"{API_BASE}/auth/login",
                    json={"username": username, "password": password},
                    timeout=10.0,
                )
            except httpx.RequestError:
                st.error("Cannot reach the server. Is it running on port 8000?")
                return

        if resp.status_code == 200:
            data = resp.json()
            st.session_state["access_token"] = data["access_token"]
            st.session_state["refresh_token"] = data["refresh_token"]
            st.session_state["username"] = username
            st.session_state["session_id"] = data.get("session_id", "")
            st.rerun()
        elif resp.status_code == 401:
            st.error("Invalid username or password")
        elif resp.status_code == 429:
            st.error("Too many attempts. Please wait and try again.")
        else:
            st.error(f"Login failed (HTTP {resp.status_code})")


def _show_dashboard():
    st.sidebar.success(f"Logged in as **{st.session_state['username']}**")
    if st.sidebar.button("Logout", type="primary"):
        _logout()
        st.rerun()

    st.subheader("Dashboard")

    with st.spinner("Fetching profile..."):
        try:
            resp = httpx.get(
                f"{API_BASE}/me",
                headers={"Authorization": f"Bearer {st.session_state['access_token']}"},
                timeout=10.0,
            )
        except httpx.RequestError:
            st.error("Connection lost")
            return

    if resp.status_code == 200:
        me = resp.json()
        st.json(me)
    elif resp.status_code == 401:
        st.warning("Session expired. Trying to refresh...")
        _try_refresh()


def _try_refresh():
    refresh_token = st.session_state.get("refresh_token")
    if not refresh_token:
        _logout()
        return

    try:
        resp = httpx.post(
            f"{API_BASE}/auth/refresh",
            json={"refresh_token": refresh_token},
            timeout=10.0,
        )
    except httpx.RequestError:
        _logout()
        return

    if resp.status_code == 200:
        data = resp.json()
        st.session_state["access_token"] = data["access_token"]
        st.session_state["refresh_token"] = data["refresh_token"]
        st.rerun()
    else:
        _logout()


def _logout():
    refresh_token = st.session_state.get("refresh_token")
    access_token = st.session_state.get("access_token")

    if refresh_token and access_token:
        try:
            httpx.post(
                f"{API_BASE}/auth/logout",
                json={"refresh_token": refresh_token},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=5.0,
            )
        except httpx.RequestError:
            pass

    for key in ("access_token", "refresh_token", "username", "session_id"):
        st.session_state.pop(key, None)


if "access_token" not in st.session_state:
    _show_login()
else:
    _show_dashboard()
