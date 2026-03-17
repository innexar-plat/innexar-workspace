"""HestiaCP REST API client: hash auth, v-add-user, v-add-web-domain, v-suspend-user, v-unsuspend-user."""
import json
import logging

import httpx

logger = logging.getLogger(__name__)


# Chaves que não são usuários/domínios/pacotes na resposta da API Hestia (metadados)
_NON_ITEM_KEYS = frozenset({
    "returncode", "answer", "json", "data", "format", "version",
    "total", "count", "limit", "offset", "status", "message",
})


def _extract_list_payload(data: dict, cmd: str = "") -> list | dict | str | None:
    """Get list payload from Hestia response. Tries 'answer', 'json', 'data'; or dict keys except returncode/metadata."""
    for key in ("answer", "json", "data"):
        if key in data and data[key] is not None:
            val = data[key]
            if isinstance(val, str) and val.strip().startswith("{"):
                try:
                    val = json.loads(val)
                except json.JSONDecodeError:
                    pass
            logger.info("Hestia %s: payload from key %r, type=%s", cmd, key, type(val).__name__)
            return val
    # Response can be {"returncode": 0, "user1": {...}, "user2": {...}} (keys = items)
    rest = {
        k: v for k, v in data.items()
        if k not in _NON_ITEM_KEYS and not k.isdigit() and isinstance(v, dict)
    }
    if rest:
        logger.info("Hestia %s: payload from top-level keys (count=%d), sample keys=%s", cmd, len(rest), list(rest.keys())[:5])
        return rest
    logger.warning("Hestia %s: no payload found. response keys=%s", cmd, list(data.keys()))
    return None


def _parse_hestia_list(answer: list | dict | str | None) -> list[dict]:
    """Normalize Hestia list response to list of dicts (name + optional fields)."""
    if answer is None:
        return []
    if isinstance(answer, list):
        return [x if isinstance(x, dict) else {"name": str(x)} for x in answer]
    if isinstance(answer, dict):
        return [{"name": k, **(v if isinstance(v, dict) else {})} for k, v in answer.items()]
    if isinstance(answer, str):
        lines = [s.strip() for s in answer.strip().split("\n") if s.strip()]
        return [{"name": line} for line in lines]
    return []


class HestiaClient:
    """Client for HestiaCP API (POST with hash=access:secret, cmd, arg1, arg2...)."""

    def __init__(self, base_url: str, access_key: str, secret_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._hash = f"{access_key}:{secret_key}"

    def request(self, cmd: str, returncode: bool = True, **args: str | int | bool) -> dict:
        """Execute Hestia command. args: arg1, arg2, arg3... (order matters for CLI)."""
        payload: dict[str, str | int] = {
            "hash": self._hash,
            "cmd": cmd,
            "returncode": "yes" if returncode else "no",
        }
        for i in range(1, 20):
            k = f"arg{i}"
            if k not in args:
                continue
            v = args[k]
            payload[k] = "yes" if v is True else "no" if v is False else str(v)
        with httpx.Client(timeout=30.0, verify=False) as client:
            resp = client.post(f"{self.base_url}/api/", data=payload)
        resp.raise_for_status()
        raw = resp.json()
        # When returncode=yes Hestia returns only the exit code in body (e.g. 0); when returncode=no the body is the full JSON.
        hestia_code = resp.headers.get("hestia-exit-code")
        if hestia_code is not None:
            try:
                code = int(hestia_code)
                if code != 0:
                    raise RuntimeError(f"Hestia {cmd} failed: exit code {code} (check Hestia-Exit-Code header)")
            except ValueError:
                pass
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, int):
            return {"returncode": raw, "answer": None}
        if isinstance(raw, str):
            if raw.strip().startswith("{"):
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    pass
            return {"returncode": 0, "answer": raw}
        return {"returncode": 1, "answer": None}

    def create_user(
        self,
        user: str,
        password: str,
        email: str,
        package: str = "default",
        first_name: str = "",
        last_name: str = "",
    ) -> None:
        """Create system user (v-add-user). CLI: v-add-user <user> <senha> <email> <package> [Nome]."""
        args: dict[str, str] = {
            "arg1": user,
            "arg2": password,
            "arg3": email,
            "arg4": package,
        }
        if first_name or last_name:
            args["arg5"] = (first_name or "").strip() or "—"
            args["arg6"] = (last_name or "").strip() or "—"
        self.request("v-add-user", **args)

    def add_web_domain(
        self,
        user: str,
        domain: str,
        ip: str = "",
        restart: str = "yes",
        aliases: str = "www",
    ) -> None:
        """Add web domain for user (v-add-web-domain)."""
        self.request(
            "v-add-web-domain",
            arg1=user,
            arg2=domain,
            arg3=ip or "default",
            arg4=restart,
            arg5=aliases,
        )

    def ensure_domain(
        self,
        user: str,
        domain: str,
        ip: str = "",
        restart: str = "yes",
        aliases: str = "www",
    ) -> None:
        """Ensure web domain exists (idempotent: add if missing; Hestia may raise if already exists)."""
        try:
            self.add_web_domain(user=user, domain=domain, ip=ip, restart=restart, aliases=aliases)
        except RuntimeError as e:
            if "already exists" in str(e).lower() or "exist" in str(e).lower():
                return
            raise

    def add_mail_domain(self, user: str, domain: str) -> None:
        """Add mail domain for user (v-add-mail-domain). HestiaCP command."""
        self.request("v-add-mail-domain", arg1=user, arg2=domain)

    def ensure_mail(self, user: str, domain: str, enabled: bool = True) -> None:
        """Ensure mail is configured for domain. When enabled=True calls v-add-mail-domain (idempotent where supported)."""
        if not enabled:
            return
        try:
            self.add_mail_domain(user=user, domain=domain)
        except RuntimeError as e:
            if "already exists" in str(e).lower() or "exist" in str(e).lower():
                return
            raise

    def healthcheck(self) -> bool:
        """Test connection (list users). Returns True if OK."""
        try:
            self.list_users()
            return True
        except Exception:
            return False

    def suspend_user(self, user: str, reason: str = "yes") -> None:
        """Suspend user (v-suspend-user)."""
        self.request("v-suspend-user", arg1=user, arg2=reason)

    def unsuspend_user(self, user: str) -> None:
        """Unsuspend user (v-unsuspend-user)."""
        self.request("v-unsuspend-user", arg1=user)

    def list_users(self) -> list:
        """List users (v-list-users). With returncode=no the API returns full JSON in body."""
        try:
            out = self.request("v-list-users", returncode=False, arg1="json")
            payload = _extract_list_payload(out, "v-list-users")
            if payload is None:
                return []
            if isinstance(payload, list):
                return payload
            result = _parse_hestia_list(payload)
            logger.info("Hestia v-list-users: %d users", len(result))
            return result
        except Exception as e:
            logger.warning("Hestia v-list-users failed: %s", e)
            return []

    def list_web_domains(self, user: str) -> list[dict]:
        """List web domains for a user (v-list-web-domains). returncode=no to get JSON in body."""
        try:
            out = self.request("v-list-web-domains", returncode=False, arg1=user, arg2="json")
            payload = _extract_list_payload(out, "v-list-web-domains")
            if payload is None:
                return []
            result = _parse_hestia_list(payload)
            logger.info("Hestia v-list-web-domains(%s): %d domains", user, len(result))
            return result
        except Exception as e:
            logger.warning("Hestia v-list-web-domains(%s) failed: %s", user, e)
            return []

    def list_packages(self) -> list[dict]:
        """List packages (v-list-user-packages). returncode=no to get JSON in body. v-list-packages does not exist."""
        try:
            out = self.request("v-list-user-packages", returncode=False, arg1="json")
            payload = _extract_list_payload(out, "v-list-user-packages")
            if payload is None:
                return []
            result = _parse_hestia_list(payload)
            logger.info("Hestia v-list-user-packages: %d packages", len(result))
            return result
        except Exception as e:
            logger.warning("Hestia v-list-user-packages failed: %s", e)
            return []

    def delete_web_domain(self, user: str, domain: str) -> None:
        """Delete web domain for user (v-delete-web-domain)."""
        self.request("v-delete-web-domain", returncode=True, arg1=user, arg2=domain)

    def delete_user(self, user: str) -> None:
        """Delete user (v-delete-user). Removes user and associated data."""
        self.request("v-delete-user", returncode=True, arg1=user)

    # ---------- Provisioning: mail DKIM, Let's Encrypt ----------

    def add_mail_domain_dkim(self, user: str, domain: str) -> None:
        """Generate DKIM for mail domain (v-add-mail-domain-dkim). Get TXT with list_mail_domain_dkim_dns."""
        self.request("v-add-mail-domain-dkim", returncode=True, arg1=user, arg2=domain)

    def list_mail_domain_dkim_dns(self, user: str, domain: str) -> dict:
        """Get DKIM DNS TXT value for mail._domainkey (v-list-mail-domain-dkim-dns). API: arg1=user, arg2=domain, arg3=json."""
        out = self.request("v-list-mail-domain-dkim-dns", returncode=True, arg1=user, arg2=domain, arg3="json")
        payload = _extract_list_payload(out)
        if isinstance(payload, dict):
            return payload
        return {}

    def add_letsencrypt_domain(self, user: str, domain: str) -> None:
        """Request Let's Encrypt SSL for domain (v-add-letsencrypt-domain). Run after DNS propagation."""
        self.request("v-add-letsencrypt-domain", returncode=True, arg1=user, arg2=domain)
