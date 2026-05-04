import base64
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class AnthemConfig:
    token_url: str
    client_id: str
    client_secret: str
    base_url: str

    @staticmethod
    def from_env() -> "AnthemConfig":
        # Token URL is provided via secure email after registration.
        # Base URL default is the CMS mandate provider directory endpoint.
        return AnthemConfig(
            token_url=_require_env("ANTHEM_TOKEN_URL"),
            client_id=_require_env("ANTHEM_CLIENT_ID"),
            client_secret=_require_env("ANTHEM_CLIENT_SECRET"),
            base_url=os.getenv(
                "ANTHEM_FHIR_BASE_URL",
                "https://totalview.healthos.elevancehealth.com/resources/unregistered/api/v1/fhir/cms_mandate/mcd/",
            ),
        )


class AnthemOAuth:
    def __init__(self, cfg: AnthemConfig):
        self._cfg = cfg
        self._cached: Optional[Tuple[str, float]] = None  # (token, expires_at_epoch)

    def get_access_token(self) -> str:
        now = time.time()
        if self._cached and (now + 30) < self._cached[1]:
            return self._cached[0]

        basic = base64.b64encode(f"{self._cfg.client_id}:{self._cfg.client_secret}".encode()).decode()
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        data = {"grant_type": "client_credentials"}

        resp = requests.post(self._cfg.token_url, headers=headers, data=data, timeout=60)
        resp.raise_for_status()
        body = resp.json()

        token = body.get("access_token")
        if not token:
            raise RuntimeError("Token endpoint response missing access_token")

        expires_in = float(body.get("expires_in") or 300.0)
        self._cached = (token, now + expires_in)
        return token


class AnthemFHIRClient:
    def __init__(self, cfg: AnthemConfig):
        self._cfg = cfg
        self._oauth = AnthemOAuth(cfg)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._oauth.get_access_token()}",
            "Accept": "application/fhir+json",
        }

    def _get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # Lightweight retries for transient gateway errors.
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
