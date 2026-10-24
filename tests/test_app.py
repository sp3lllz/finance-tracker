import re
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from app import csvio
from app.config import Settings
from app.db import Database, totals_for
from app.main import create_app
from app.money import format_gbp, parse_pence
from app.structure import BLANK_MONTH

SECRET = "x" * 40


def settings(tmp_path, **overrides):
    base = dict(data_dir=tmp_path, session_secret=SECRET, oidc_issuer="https://id.test",
                oidc_client_id="client-123", app_url="http://testserver")
    base.update(overrides)
    return Settings(**base)


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


# --- units -------------------------------------------------------------------

@pytest.mark.parametrize("text,pence", [
    ("£1,234.56", 123456), ("12", 1200), ("-3.5", -350), ("", 0), ("0.005", 1),
])
def test_parse_pence(text, pence):
    assert parse_pence(text) == pence


def test_parse_pence_rejects_junk():
    with pytest.raises(ValueError):
        parse_pence("twelve")


def test_format_gbp():
    assert format_gbp(123456) == "£1,234.56"
    assert format_gbp(-1769) == "-£17.69"


def test_blank_and_copy_month(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_blank_month(2026, 1)
    assert len(db.items(2026, 1)) == len(BLANK_MONTH)
    salary = next(i for i in db.items(2026, 1) if i.kind == "income")
    db.update_item(salary.id, salary.name, 200000)
    db.add_item(2026, 1, "bill", "Utilities", "Water", 4000)
    db.add_item(2026, 1, "planned", "Planned spending", "Sofa", 30000)

    assert db.previous_month(2026, 3) == (2026, 1)
    db.copy_month((2026, 1), (2026, 3))
    copied = totals_for(db.items(2026, 3))
    assert (copied.income, copied.bills, copied.planned) == (200000, 4000, 0)
    assert copied.after_spending == 196000


def test_csv_round_trip(tmp_path):
    db = Database(tmp_path / "t.db")
    db.add_item(2026, 2, "income", "Income", "Wages", 150000)
    db.add_item(2026, 2, "bill", "Housing, rent", 'Rent "flat"', 60000)
    text = csvio.export_csv(db.all_items())
    other = Database(tmp_path / "u.db")
    other.replace_months(csvio.parse_csv(text))
    assert [(i.category, i.name, i.amount_pence) for i in other.all_items()] == \
           [("Income", "Wages", 150000), ("Housing, rent", 'Rent "flat"', 60000)]


def test_csv_bad_line_imports_nothing():
    with pytest.raises(csvio.ImportError_, match="Line 3"):
        csvio.parse_csv("year,month,kind,category,name,amount\n"
                        "2026,1,bill,Housing,Rent,10\n"
                        "2026,13,bill,Housing,Rent,10\n")


# --- app with login disabled -------------------------------------------------

@pytest.fixture
def local(tmp_path):
    return TestClient(create_app(settings(tmp_path, auth_disabled=True)))


def test_month_lifecycle(local):
    page = local.get("/month/2026/5")
    assert "hasn't been started" in page.text
    token = csrf_of(page.text)

    local.post("/month/2026/5/start", data={"csrf": token, "mode": "blank"})
    page = local.get("/month/2026/5")
    assert "Council tax" in page.text

    local.post("/month/2026/5/items", data={"csrf": token, "kind": "income",
                                            "name": "Bonus", "amount": "£1,000"})
    local.post("/month/2026/5/items", data={"csrf": token, "kind": "planned",
                                            "name": "Bike", "amount": "250"})
    page = local.get("/month/2026/5")
    assert "£1,000.00" in page.text and "£750.00" in page.text  # left over after spending

    # "Copy last month" offers May as the source for June.
    assert "Copy from May 2026" in local.get("/month/2026/6").text
    local.post("/month/2026/6/start", data={"csrf": token, "mode": "copy"})
    june = local.get("/month/2026/6").text
    assert "Bonus" in june and "Bike" not in june

    year = local.get("/year/2026").text
    assert "<svg" in year and "Not started" in year

    export = local.get("/data/export.csv")
    assert export.headers["content-type"].startswith("text/csv")
    assert "2026,5,income,Income,Bonus,1000.00" in export.text


def test_post_without_csrf_is_rejected(local):
    local.get("/month/2026/5")
    r = local.post("/month/2026/5/start", data={"mode": "blank"})
    assert r.status_code == 403
    assert "hasn't been started" in local.get("/month/2026/5").text


def test_import_replaces_only_listed_months(local):
    token = csrf_of(local.get("/data").text)
    local.post("/month/2026/1/start", data={"csrf": token, "mode": "blank"})
    local.post("/month/2026/2/start", data={"csrf": token, "mode": "blank"})
    csv_text = "year,month,kind,category,name,amount\n2026,2,income,Income,Pay,99.50\n"
    r = local.post("/data/import", data={"csrf": token},
                   files={"file": ("in.csv", csv_text, "text/csv")})
    assert "Imported 1 lines across 1 month" in r.text
    assert "Council tax" in local.get("/month/2026/1").text
    feb = local.get("/month/2026/2").text
    assert "Pay" in feb and "Council tax" not in feb


def test_security_headers(local):
    r = local.get("/year/2026")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


# --- app with Pocket ID login ------------------------------------------------

METADATA = {
    "_loaded_at": 1,
    "issuer": "https://id.test",
    "authorization_endpoint": "https://id.test/authorize",
    "token_endpoint": "https://id.test/api/oidc/token",
    "end_session_endpoint": "https://id.test/api/oidc/end-session",
    "jwks_uri": "https://id.test/.well-known/jwks.json",
}


def oidc_client(tmp_path, claims, **overrides):
    app = create_app(settings(tmp_path, **overrides))
    auth_routes = {r.path: r for r in app.routes if hasattr(r, "endpoint")}
    oidc = auth_routes["/login"].endpoint.__self__.oauth.oidc
    oidc.server_metadata = dict(METADATA)

    async def fake_token(request, **kwargs):
        return {"id_token": "idtok", "userinfo": claims}

    oidc.authorize_access_token = fake_token
    return TestClient(app, follow_redirects=False)


def test_requires_login_and_signs_in(tmp_path):
    client = oidc_client(tmp_path, {"sub": "u1", "name": "Sam", "email": "sam@example.com"})
    r = client.get("/month/2026/3")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/month/2026/3"
    assert client.post("/month/2026/3/start").status_code == 401
    assert client.get("/healthz").status_code == 200

    r = client.get("/login?next=/month/2026/3")
    authorize = urlparse(r.headers["location"])
    params = parse_qs(authorize.query)
    assert authorize.netloc == "id.test"
    assert params["client_id"] == ["client-123"]
    assert params["redirect_uri"] == ["http://testserver/auth/callback"]
    assert params["code_challenge_method"] == ["S256"]
    assert "groups" in params["scope"][0]

    r = client.get("/auth/callback?code=abc&state=" + params["state"][0])
    assert r.status_code == 303 and r.headers["location"] == "/month/2026/3"
    assert "Sam" in client.get("/month/2026/3").text

    r = client.post("/logout")
    assert r.headers["location"].startswith("https://id.test/api/oidc/end-session?")
    assert client.get("/year/2026").status_code == 303


def test_group_restriction(tmp_path):
    client = oidc_client(tmp_path, {"sub": "u2", "groups": ["family"]},
                         allowed_groups=["finance"])
    client.get("/login")
    assert client.get("/auth/callback?code=abc").status_code == 403
    assert client.get("/year/2026").status_code == 303


def test_open_redirect_blocked(tmp_path):
    client = oidc_client(tmp_path, {"sub": "u3"})
    client.get("/login?next=//evil.example/")
    assert client.get("/auth/callback?code=abc").headers["location"] == "/"


def test_config_requires_secret_and_issuer(tmp_path):
    with pytest.raises(SystemExit, match="SESSION_SECRET"):
        settings(tmp_path, session_secret="short").validate()
    with pytest.raises(SystemExit, match="OIDC_ISSUER"):
        settings(tmp_path, oidc_issuer="").validate()
