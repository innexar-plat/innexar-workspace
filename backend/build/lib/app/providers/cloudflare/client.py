"""Cloudflare API v4 client: zones and DNS records (Bearer token)."""
from __future__ import annotations

import httpx

BASE_URL = "https://api.cloudflare.com/client/v4"


class CloudflareClient:
    """Client for Cloudflare API v4 (zones, DNS records). Uses API Token (Bearer)."""

    def __init__(self, api_token: str, account_id: str | None = None) -> None:
        self._api_token = api_token
        self._account_id = account_id or ""

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_token}", "Content-Type": "application/json"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict | None = None,
    ) -> dict:
        url = f"{BASE_URL}{path}"
        with httpx.Client(timeout=30.0) as client:
            resp = client.request(method, url, headers=self._headers(), params=params, json=json)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success", True):
            errors = data.get("errors", [])
            msg = "; ".join(e.get("message", str(e)) for e in errors) if errors else "Unknown error"
            raise RuntimeError(f"Cloudflare API error: {msg}")
        return data

    def list_zones(self, name: str | None = None) -> list[dict]:
        """List zones. If name is given, filter by zone name (exact or suffix)."""
        data = self._request("GET", "/zones", params={"name": name} if name else None)
        return list(data.get("result", []))

    def get_zone_by_name(self, name: str) -> dict | None:
        """Return zone dict if a zone with this name exists (first match)."""
        # API accepts name=example.com and returns matching zones
        data = self._request("GET", f"/zones?name={name}")
        results = data.get("result", [])
        for z in results:
            if z.get("name") == name:
                return z
        return None

    def create_zone(self, name: str, account_id: str | None = None, zone_type: str = "full") -> dict:
        """Create a zone. Requires account_id (from constructor or param). Returns zone dict with id, name, name_servers."""
        aid = account_id or self._account_id
        if not aid:
            raise ValueError("account_id required to create zone")
        payload = {"name": name, "account": {"id": aid}, "type": zone_type}
        data = self._request("POST", "/zones", json=payload)
        return data.get("result", {})

    def create_dns_record(
        self,
        zone_id: str,
        record_type: str,
        name: str,
        content: str,
        ttl: int = 1,
        proxied: bool = False,
        priority: int | None = None,
    ) -> dict:
        """Create a DNS record. name can be '@' or 'www' or full FQDN. ttl 1 = auto."""
        body: dict[str, str | int | bool] = {
            "type": record_type,
            "name": name,
            "content": content,
            "ttl": ttl,
            "proxied": proxied,
        }
        if priority is not None and record_type.upper() in ("MX", "SRV"):
            body["priority"] = priority
        data = self._request("POST", f"/zones/{zone_id}/dns_records", json=body)
        return data.get("result", {})
