import os
from dataclasses import dataclass, field
from pathlib import Path


def _list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass
class Settings:
    app_url: str = "http://localhost:8000"
    data_dir: Path = Path("/data")
    session_secret: str = ""
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid profile email groups"
    allowed_groups: list[str] = field(default_factory=list)
    allowed_emails: list[str] = field(default_factory=list)
    auth_disabled: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        settings = cls(
            app_url=env.get("APP_URL", cls.app_url).rstrip("/"),
            data_dir=Path(env.get("DATA_DIR", str(cls.data_dir))),
            session_secret=env.get("SESSION_SECRET", ""),
            oidc_issuer=env.get("OIDC_ISSUER", "").rstrip("/"),
            oidc_client_id=env.get("OIDC_CLIENT_ID", ""),
            oidc_client_secret=env.get("OIDC_CLIENT_SECRET", ""),
            oidc_scopes=env.get("OIDC_SCOPES", cls.oidc_scopes),
            allowed_groups=_list(env.get("OIDC_ALLOWED_GROUPS", "")),
            allowed_emails=[e.lower() for e in _list(env.get("OIDC_ALLOWED_EMAILS", ""))],
            auth_disabled=env.get("AUTH_DISABLED", "").lower() in ("1", "true", "yes"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        problems = []
        if len(self.session_secret) < 32:
            problems.append("SESSION_SECRET must be set to at least 32 random characters")
        if not self.auth_disabled:
            if not self.oidc_issuer:
                problems.append("OIDC_ISSUER is required (your Pocket ID URL)")
            if not self.oidc_client_id:
                problems.append("OIDC_CLIENT_ID is required")
        if problems:
            raise SystemExit("Configuration error:\n  - " + "\n  - ".join(problems))

    @property
    def secure_cookies(self) -> bool:
        return self.app_url.startswith("https://")
