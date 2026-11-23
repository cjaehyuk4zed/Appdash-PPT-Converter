import time

import pytest
from starlette.testclient import TestClient

from converter.server import Config, create_app
from converter.worker import run_process


@pytest.fixture
def app(tmp_path):
    return create_app(Config(data_dir=tmp_path / "runtime", allowed_hosts=["testserver"], timeout=60))


def upload(client, path):
    client.get("/")
    with path.open("rb") as stream:
        response = client.post("/api/files", files={"files": (path.name, stream, "application/octet-stream")})
    assert response.status_code == 200, response.text
    assert not response.json()["errors"], response.text
    return response.json()["files"][0]


def wait(client, job_id):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        value = client.get(f"/api/jobs/{job_id}").json()
        if value["state"] not in ("queued", "running"):
            return value
        time.sleep(.05)
    pytest.fail("Job did not finish")


def test_api_real_worker_downloads_and_session_isolation(app, image_path):
    with TestClient(app) as client:
        ready = client.get("/api/ready")
        assert ready.status_code == 200 and "avif" in ready.json()["formats"]["output"]
        asset = upload(client, image_path)
        response = client.post("/api/jobs", json={"selections": [{"file_id": asset["id"]}], "options": {"operation": "image_convert", "format": "webp"}})
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        result = wait(client, job_id)
        assert result["state"] == "completed", result
        output = client.get(f"/api/jobs/{job_id}/outputs/0")
        assert len(output.content) == result["outputs"][0]["size"]
        assert "attachment" in output.headers["content-disposition"]
        assert client.get(f"/api/jobs/{job_id}/zip").content[:2] == b"PK"
        assert client.get(f"/api/jobs/{job_id}/preview").content[:4] == b"\x89PNG"
        cookie = client.cookies.get("ppt_converter_session")
        client.cookies.clear()
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
        assert client.get(f"/api/files/{asset['id']}/thumbnail").status_code == 404
        client.cookies.set("ppt_converter_session", cookie)
        assert client.delete(f"/api/jobs/{job_id}").status_code == 200
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
        assert client.delete(f"/api/files/{asset['id']}").status_code == 200


def test_security_boundaries_and_invalid_inputs(app, image_path):
    with TestClient(app) as client:
        assert client.post("/api/jobs", json={}).status_code == 403
        client.get("/")
        assert client.post("/api/jobs", json={}, headers={"origin": "https://evil.example"}).status_code == 403
        assert client.get("/api/ready", headers={"host": "evil.example"}).status_code == 400
        assert client.post("/api/jobs", json={}).status_code == 422
        assert client.post("/api/files", files={"files": ("bad.pdf", b"not a pdf")}).json()["errors"]
        asset = upload(client, image_path)
        response = client.post("/api/jobs", json={"selections": [{"file_id": asset["id"]}], "options": {"operation": "ppt_to_images"}})
        assert response.status_code == 422
        assert "password" not in str(client.get("/api/ready").json())


def test_cancel_queued_and_active_jobs_and_delete_guard(app, noise_path):
    with TestClient(app) as client:
        asset = upload(client, noise_path)
        body = {"selections": [{"file_id": asset["id"]}] * 25, "options": {"operation": "image_convert", "format": "avif"}}
        first = client.post("/api/jobs", json=body).json()["id"]
        second = client.post("/api/jobs", json=body).json()["id"]
        assert client.delete(f"/api/files/{asset['id']}").status_code == 409
        client.post(f"/api/jobs/{second}/cancel")
        client.post(f"/api/jobs/{first}/cancel")
        assert wait(client, first)["state"] == "cancelled"
        assert wait(client, second)["state"] == "cancelled"
        assert client.get(f"/api/jobs/{first}/outputs/0").status_code == 409


def test_retention_cleanup(app, image_path):
    with TestClient(app) as client:
        asset = upload(client, image_path)
        store = app.state.store
        directory = store.assets[asset["id"]]["path"]
        store.assets[asset["id"]]["created"] = time.time() - store.config.ttl - 1
        store.cleanup()
        from pathlib import Path
        assert not Path(directory).exists()
        assert client.get(f"/api/files/{asset['id']}/thumbnail").status_code == 404


def test_request_and_per_file_limits(tmp_path, image_path):
    small = create_app(Config(data_dir=tmp_path / "limited", allowed_hosts=["testserver"], max_file_bytes=50, max_request_bytes=10000))
    with TestClient(small) as client:
        client.get("/")
        assert client.post("/api/files", content=b"a" * 10001).status_code == 413
        response = client.post("/api/files", files={"files": ("large.png", b"a" * 100)})
        assert "per-file" in response.json()["errors"][0]["message"]


def test_worker_timeout(image_path, tmp_path):
    payload = {"path": str(image_path), "thumbnail": str(tmp_path / "thumb.png"), "pixel_limit": 40000000, "page_limit": 500}
    with pytest.raises(TimeoutError):
        run_process("inspect", payload, timeout=0)


def test_chunked_request_limit(tmp_path):
    app = create_app(Config(data_dir=tmp_path / "chunked", allowed_hosts=["testserver"], max_request_bytes=1000))
    with TestClient(app) as client:
        client.get("/")
        response = client.post("/api/jobs", content=iter([b"{" + b" " * 500, b" " * 600 + b"}"]), headers={"content-type": "application/json"})
        assert response.status_code == 413


def test_tiff_page_card_dimensions_thumbnails_cache_and_validation(app, tmp_path):
    import io
    from pathlib import Path

    from PIL import Image

    path = tmp_path / 'pages.tiff'
    with Image.new('RGB', (120, 160), 'red') as first, Image.new('RGB', (220, 140), 'blue') as second:
        first.save(path, save_all=True, append_images=[second])
    with TestClient(app) as client:
        asset = upload(client, path)
        assert asset['page_sizes'] == [[120, 160], [220, 140]]
        url = f"/api/files/{asset['id']}/thumbnail"
        for page, color in [(0, (255, 0, 0)), (1, (0, 0, 255))]:
            response = client.get(url, params={'page': page})
            assert response.status_code == 200
            with Image.open(io.BytesIO(response.content)) as thumbnail:
                assert thumbnail.size == tuple(asset['page_sizes'][page])
                assert thumbnail.convert('RGB').getpixel((10, 10)) == color
        cached = Path(app.state.store.assets[asset['id']]['path']).parent / 'thumbnail-1.png'
        modified = cached.stat().st_mtime_ns
        assert client.get(url, params={'page': 1}).status_code == 200
        assert cached.stat().st_mtime_ns == modified
        for page in (-1, 2, 'oops', '1.5'):
            assert client.get(url, params={'page': page}).status_code == 422
        client.cookies.clear()
        assert client.get(url, params={'page': 1}).status_code == 404
