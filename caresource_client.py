import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


@dataclass(frozen=True)
class CareSourceConfig:
    """
    CareSource Provider Directory (Plan-Net) is not publicly documented with a base URL.
    After registration, configure the base URL and auth details via env vars.
    """

    base_url: str
    bearer_token: Optional[str] = None
    token_url: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    scope: Optional[str] = None

    @staticmethod
    def from_env() -> "CareSourceConfig":
        base_url = os.getenv("CARESOURCE_FHIR_BASE_URL")
        if not base_url:
            raise RuntimeError("Missing CARESOURCE_FHIR_BASE_URL (provided after CareSource registration).")
        return CareSourceConfig(
            base_url=base_url,
            bearer_token=os.getenv("CARESOURCE_BEARER_TOKEN") or None,
            token_url=os.getenv("CARESOURCE_TOKEN_URL") or None,
            client_id=os.getenv("CARESOURCE_CLIENT_ID") or None,
            client_secret=os.getenv("CARESOURCE_CLIENT_SECRET") or None,
            scope=os.getenv("CARESOURCE_SCOPE") or None,
        )


class _ClientCredentialsOAuth:
    def __init__(self, cfg: CareSourceConfig):
        self._cfg = cfg
        self._cached: Optional[tuple[str, float]] = None  # token, expires_at

    def configured(self) -> bool:
        return bool(self._cfg.token_url and self._cfg.client_id and self._cfg.client_secret)

    def get_access_token(self) -> str:
        if not self.configured():
            raise RuntimeError("OAuth client_credentials not configured")

        now = time.time()
        if self._cached and (now + 30) < self._cached[1]:
            return self._cached[0]

        data = {"grant_type": "client_credentials"}
        if self._cfg.scope:
            data["scope"] = self._cfg.scope

        resp = requests.post(
            self._cfg.token_url,  # type: ignore[arg-type]
            data=data,
            auth=(self._cfg.client_id, self._cfg.client_secret),  # type: ignore[arg-type]
            headers={"Accept": "application/json"},
            timeout=60,
        )
        resp.raise_for_status()
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise RuntimeError("Token endpoint response missing access_token")

        expires_in = float(body.get("expires_in") or 300.0)
        self._cached = (token, now + expires_in)
        return token


class CareSourceFHIRClient:
    def __init__(self, cfg: CareSourceConfig):
        self._cfg = cfg
        self._oauth = _ClientCredentialsOAuth(cfg)

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/fhir+json"}
        token = self._cfg.bearer_token
        if not token and self._oauth.configured():
            token = self._oauth.get_access_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        retries = 3
        backoff = 1.0
        last: Optional[requests.Response] = None

        for attempt in range(1, retries + 1):
            try:
                resp = requests.get(url, headers=self._headers(), params=params, timeout=60)
                last = resp
            except requests.RequestException:
                if attempt == retries:
                    raise
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code in (429, 502, 503, 504) and attempt < retries:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        time.sleep(float(retry_after))
                        continue
                    except ValueError:
                        pass
                time.sleep(backoff)
                backoff *= 2
                continue

            resp.raise_for_status()

        assert last is not None
        last.raise_for_status()
        raise RuntimeError("Unreachable")

    def iter_entries(
        self,
        resource_type: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        count_per_page: int = 200,
        max_pages: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []

        base = self._cfg.base_url.rstrip("/") + "/"
        url: Optional[str] = f"{base}{resource_type}"
        cur_params: Optional[Dict[str, Any]] = dict(params or {})
        cur_params["_count"] = count_per_page

        page = 1
        while url:
            body = self._get_json(url, params=cur_params)
            entries.extend(body.get("entry", []))

            if max_pages is not None and page >= max_pages:
                break

            next_url = None
            for link in body.get("link", []):
                if link.get("relation") == "next":
                    next_url = link.get("url")
                    break

            url = next_url
            cur_params = None
            page += 1

        return entries

