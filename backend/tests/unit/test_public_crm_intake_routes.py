from pathlib import Path
import sys


def _ensure_backend_path() -> None:
    backend_dir = Path(__file__).resolve().parents[2]
    build_lib = backend_dir / "build" / "lib"
    if str(build_lib) not in sys.path:
        sys.path.insert(0, str(build_lib))


_ensure_backend_path()

from app.api.public_router import router  # noqa: E402


def test_public_router_exposes_contact_submit_and_web_to_lead() -> None:
    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/web-to-lead" in paths
    assert "/contact/submit" in paths
