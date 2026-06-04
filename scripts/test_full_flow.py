import requests as r

# Login
l = r.post("http://localhost:8000/api/v1/auth/login",
    json={"username": "testuser", "password": "testpassword", "hospital_id": "hosp-001"}).json()
print("1. Login:", "OK" if "access_token" in l else l)

# Refresh
t = r.post("http://localhost:8000/api/v1/auth/refresh",
    json={"refresh_token": l["refresh_token"]}).json()
print("2. Refresh:", "OK" if "access_token" in t else t.get("detail", t))

# Logout — use same tokens as login (don't refresh between login and logout)
o = r.post("http://localhost:8000/api/v1/auth/logout",
    json={"refresh_token": l["refresh_token"]},
    headers={"Authorization": f"Bearer {l['access_token']}"})
print("3. Logout:", o.status_code, o.text[:200])

# Refresh after logout
t2 = r.post("http://localhost:8000/api/v1/auth/refresh",
    json={"refresh_token": l["refresh_token"]}).json()
print("4. Refresh after logout:", t2.get("detail", "OK" if "access_token" in t2 else "?"))
