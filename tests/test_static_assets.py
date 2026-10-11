from fastapi.testclient import TestClient

from openspline_server import app as server_app
from openspline_server.config import Settings


def test_static_assets_follow_uv_install_symlinks(tmp_path, monkeypatch):
    static = tmp_path / "installed" / "static"
    cache = tmp_path / "cache"
    static.mkdir(parents=True)
    cache.mkdir()
    (cache / "style.css").write_text("body { color: rebeccapurple; }")
    (static / "style.css").symlink_to(cache / "style.css")
    monkeypatch.setattr(server_app, "STATIC", static)

    client = TestClient(
        server_app.create_app(Settings(backend="test", runtime_dir=str(tmp_path / "runtime")))
    )
    response = client.get("/static/style.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.text == "body { color: rebeccapurple; }"
