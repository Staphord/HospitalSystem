import httpx

def test_login(username, password, endpoint, port):
    url = f"http://localhost:{port}/api/v1/auth/{endpoint}"
    payload = {
        "username": username,
        "password": password
    }
    print(f"Testing POST {url} for '{username}'...")
    try:
        response = httpx.post(url, json=payload, timeout=10.0)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
    except Exception as e:
        print(f"Request failed: {e}")
    print("-" * 50)

def main():
    for port in [8001, 8000]:
        print(f"=== TESTING PORT {port} ===")
        # Test Super Admin login
        test_login("superadmin", "superadmin123", "superadmin/login", port)
        
        # Test Admin User login
        test_login("adminuser", "adminpassword", "login", port)

if __name__ == "__main__":
    main()