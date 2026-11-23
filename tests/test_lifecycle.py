"""Start the real server and finish a conversion over HTTP on both operating systems."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def test_real_server_lifecycle_and_conversion(tmp_path, image_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = {**os.environ, "CONVERTER_HOST": "127.0.0.1", "CONVERTER_PORT": str(port), "CONVERTER_DATA_DIR": str(tmp_path / "live-runtime"), "CONVERTER_ALLOWED_HOSTS": "127.0.0.1"}
    root = Path(__file__).resolve().parent.parent
    with (tmp_path / "server.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, "run.py"], cwd=root, env=environment, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
                deadline = time.monotonic() + 30
                while True:
                    assert process.poll() is None, (tmp_path / "server.log").read_text()
                    try:
                        if client.get("/api/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, "Server readiness timed out"
                    time.sleep(.1)
                with image_path.open("rb") as stream:
                    asset = client.post("/api/files", files={"files": (image_path.name, stream)}).json()["files"][0]
                response = client.post("/api/jobs", json={"selections": [{"file_id": asset["id"]}], "options": {"operation": "images_to_ppt"}})
                assert response.status_code == 202, response.text
                job_id = response.json()["id"]
                while True:
                    job = client.get(f"/api/jobs/{job_id}").json()
                    if job["state"] not in ("queued", "running"):
                        break
                    assert time.monotonic() < deadline
                    time.sleep(.1)
                assert job["state"] == "completed", job
                assert client.get(f"/api/jobs/{job_id}/outputs/0").content.startswith(b"PK")
                assert client.delete(f"/api/jobs/{job_id}").status_code == 200
                assert client.delete(f"/api/files/{asset['id']}").status_code == 200
        finally:
            if os.name == "nt":
                # A Windows venv launcher may own a second Python process; stop only our tree.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=True, capture_output=True)
            else:
                process.terminate()
            process.wait(timeout=15)
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0
