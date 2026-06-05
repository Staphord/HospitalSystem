from jose import jwt
from jose.constants import Algorithms
import httpx

resp = httpx.get("http://127.0.0.1:8080/realms/hospital-realm/protocol/openid-connect/certs")
jwks = resp.json()

signing_key = None
for k in jwks.get("keys", []):
    if k.get("use") == "sig" and k.get("kty") == "RSA":
        signing_key = k
        break

print(f"signing_key kid: {signing_key.get('kid')}")
print(f"signing_key has d: {'d' in signing_key}")
print(f"signing_key keys: {list(signing_key.keys())}")

try:
    token = jwt.encode({"sub": "test"}, signing_key, algorithm=Algorithms.RS256, headers={"kid": signing_key.get("kid")})
    print(f"Token: {token[:50]}...")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")
