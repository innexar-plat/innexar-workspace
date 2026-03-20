from pathlib import Path
import sys


def _ensure_backend_path() -> None:
    backend_dir = Path(__file__).resolve().parents[2]
    build_lib = backend_dir / "build" / "lib"
    if str(build_lib) not in sys.path:
        sys.path.insert(0, str(build_lib))


_ensure_backend_path()

from app.modules.crm.router_workspace import router  # noqa: E402


def test_crm_router_exposes_summary_endpoint() -> None:
    paths = {getattr(route, "path", "") for route in router.routes}
    assert "/crm/summary" in paths
