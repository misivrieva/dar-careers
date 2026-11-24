import pytest


def test_guard_off_locally(client):
    assert client.get("/api/status").status_code == 200


def test_shared_secret_required_when_configured(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "api_shared_secret", "s3cret-value")
    assert client.get("/api/health").status_code == 200          # health stays open for the platform
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"X-DAR-Secret": "wrong"}).status_code == 401
    assert client.get("/api/status", headers={"X-DAR-Secret": "s3cret-value"}).status_code == 200


def test_access_jwt_required_when_configured(client, monkeypatch):
    from app import security
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "cf_access_team_domain", "team.cloudflareaccess.com")
    monkeypatch.setattr(s, "cf_access_aud", "aud123")
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"Cf-Access-Jwt-Assertion": "not-a-jwt"}).status_code == 401
    monkeypatch.setattr(security, "verify_access_jwt", lambda t: {"email": "mims@example.com"})
    assert client.get("/api/status", headers={"Cf-Access-Jwt-Assertion": "x.y.z"}).status_code == 200


def test_access_jwt_verification_with_real_signature(monkeypatch):
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app import security
    from app.config import get_settings

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    s = get_settings()
    monkeypatch.setattr(s, "cf_access_team_domain", "team.cloudflareaccess.com")
    monkeypatch.setattr(s, "cf_access_aud", "aud123")
    monkeypatch.setattr(s, "allowed_emails", "mims@example.com")
    monkeypatch.setattr(security, "_keys", lambda team: {"k1": key.public_key()})
    good = pyjwt.encode({"aud": "aud123", "iss": "https://team.cloudflareaccess.com", "email": "mims@example.com", "exp": 9999999999},
                        key, algorithm="RS256", headers={"kid": "k1"})
    assert security.verify_access_jwt(good)["email"] == "mims@example.com"
    other = pyjwt.encode({"aud": "aud123", "iss": "https://team.cloudflareaccess.com", "email": "x@evil.com", "exp": 9999999999},
                         key, algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(Exception):
        security.verify_access_jwt(other)
    wrong_aud = pyjwt.encode({"aud": "other", "iss": "https://team.cloudflareaccess.com", "email": "mims@example.com", "exp": 9999999999},
                             key, algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(Exception):
        security.verify_access_jwt(wrong_aud)


def test_refuses_to_start_open_when_auth_required(monkeypatch):
    from app import security
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "require_auth", True)
    with pytest.raises(security.AccessConfigError):
        security.check_config()


def test_tunnel_traffic_never_counts_as_local(monkeypatch):
    from starlette.requests import Request

    from app.security import _is_local_direct

    def req(headers):
        return Request({"type": "http", "client": ("127.0.0.1", 5000), "headers": [(k.encode(), v.encode()) for k, v in headers.items()]})

    assert _is_local_direct(req({}))
    assert not _is_local_direct(req({"cf-connecting-ip": "203.0.113.7"}))
    assert not _is_local_direct(req({"cf-ray": "abc"}))
