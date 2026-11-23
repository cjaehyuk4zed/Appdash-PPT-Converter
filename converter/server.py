import asyncio
import hashlib
import hmac
import os
import re
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles

from .models import JobRequest
from .worker import run_process

BASE = Path(__file__).resolve().parent.parent
IDENTIFIER = re.compile(r"^[a-f0-9]{32}$")


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CONVERTER_DATA_DIR", str(BASE / ".runtime"))).resolve())
    max_file_bytes: int = field(default_factory=lambda: int(os.environ.get("CONVERTER_MAX_FILE_MB", "100")) * 1000000)
    max_request_bytes: int = field(default_factory=lambda: int(os.environ.get("CONVERTER_MAX_BATCH_MB", "250")) * 1000000)
    max_pixels: int = field(default_factory=lambda: int(os.environ.get("CONVERTER_MAX_PIXELS", "60000000")))
    max_pages: int = field(default_factory=lambda: int(os.environ.get("CONVERTER_MAX_PAGES", "500")))
    max_output_bytes: int = field(default_factory=lambda: int(os.environ.get("CONVERTER_MAX_OUTPUT_MB", "500")) * 1000000)
    timeout: int = field(default_factory=lambda: int(os.environ.get("CONVERTER_JOB_TIMEOUT", "600")))
    ttl: int = field(default_factory=lambda: int(os.environ.get("CONVERTER_RETENTION_SECONDS", "3600")))
    max_queue: int = 8
    allowed_hosts: list[str] = field(default_factory=lambda: [v.strip() for v in os.environ.get("CONVERTER_ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]").split(",") if v.strip()])
    public_origins: list[str] = field(default_factory=lambda: [v.strip().rstrip("/") for v in os.environ.get("CONVERTER_PUBLIC_ORIGINS", "").split(",") if v.strip()])
    secure_cookie: bool = field(default_factory=lambda: os.environ.get("CONVERTER_SECURE_COOKIE", "0") == "1")


class Store:
    def __init__(self, config):
        self.config = config
        self.lock = threading.RLock()
        self.assets = {}
        self.jobs = {}
        self.shutdown_event = threading.Event()
        self.inspection_slots = threading.BoundedSemaphore(1)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="conversion")
        for group in ("uploads", "jobs"):
            (config.data_dir / group).mkdir(parents=True, exist_ok=True)
        self.reaper = threading.Thread(target=self._reap_loop, daemon=True)

    def start(self):
        self.cleanup()
        self.reaper.start()

    def close(self):
        self.shutdown_event.set()
        with self.lock:
            for job in self.jobs.values():
                job["cancel"].set()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.reaper.join(timeout=2)

    def _reap_loop(self):
        while not self.shutdown_event.wait(30):
            self.cleanup()

    def _remove(self, path):
        resolved = Path(path).resolve()
        parents = [(self.config.data_dir / group).resolve() for group in ("uploads", "jobs")]
        if resolved.parent not in parents or not IDENTIFIER.fullmatch(resolved.name):
            raise RuntimeError("Refusing to delete an unmanaged directory.")
        # Unlink a directory junction/symlink itself; never traverse its target.
        if Path(path).is_symlink() or getattr(Path(path), "is_junction", lambda: False)():
            return
        shutil.rmtree(resolved, ignore_errors=True)

    def cleanup(self):
        cutoff = time.time() - self.config.ttl
        with self.lock:
            for job_id, job in list(self.jobs.items()):
                if job["state"] not in ("queued", "running") and job["updated"] < cutoff:
                    self._remove(job["directory"])
                    del self.jobs[job_id]
            referenced = {item["file_id"] for job in self.jobs.values() if job["state"] in ("queued", "running") for item in job["items"]}
            for asset_id, asset in list(self.assets.items()):
                if asset["created"] < cutoff and asset_id not in referenced:
                    self._remove(Path(asset["path"]).parent)
                    del self.assets[asset_id]
            known = set(self.assets) | set(self.jobs)
            for group in ("uploads", "jobs"):
                for path in (self.config.data_dir / group).iterdir():
                    if IDENTIFIER.fullmatch(path.name) and path.name not in known and path.stat().st_mtime < cutoff:
                        self._remove(path)

    def asset(self, asset_id, owner):
        with self.lock:
            asset = self.assets.get(asset_id)
            if not asset or asset["owner"] != owner:
                raise HTTPException(404, "File expired or not found in this browser session.")
            return asset

    def job(self, job_id, owner):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job["owner"] != owner:
                raise HTTPException(404, "Job expired or not found in this browser session.")
            return job

    def public_job(self, job):
        with self.lock:
            return {key: job.get(key) for key in ("id", "state", "progress", "detail", "error", "outputs", "warnings", "preview", "created", "updated", "input_bytes")}

    def submit(self, request, owner):
        with self.lock:
            if sum(job["state"] in ("queued", "running") for job in self.jobs.values()) >= self.config.max_queue:
                raise HTTPException(429, "The conversion queue is full. Wait for a job to finish.")
            items = []
            for selection in request.selections:
                asset = self.asset(selection.file_id, owner)
                if selection.page >= asset["pages"]:
                    raise HTTPException(422, "Selected page does not exist.")
                requires_pdf = request.options.operation in ("ppt_to_images", "ppt_compress")
                if (asset["kind"] == "ppt") != requires_pdf:
                    raise HTTPException(422, "This tool requires PPT files." if requires_pdf else "This tool requires image files.")
                items.append({**asset, "file_id": selection.file_id, "page": selection.page, "rotation": selection.rotation})
            if len(items) > self.config.max_pages:
                raise HTTPException(422, "Too many selected pages.")
            job_id = secrets.token_hex(16)
            directory = self.config.data_dir / "jobs" / job_id
            directory.mkdir()
            job = {"id": job_id, "owner": owner, "state": "queued", "progress": 0, "detail": "Waiting for the conversion worker", "created": time.time(), "updated": time.time(), "directory": str(directory), "items": items, "cancel": threading.Event(), "options": request.options.model_dump(), "preview": request.preview, "input_bytes": sum(self.assets[file_id]["size"] for file_id in {item["file_id"] for item in items})}
            self.jobs[job_id] = job
            self.pool.submit(self._execute, job)
            return self.public_job(job)

    def _execute(self, job):
        with self.lock:
            if job["cancel"].is_set():
                job.update(state="cancelled", detail="Cancelled", updated=time.time())
                return
            job.update(state="running", detail="Starting conversion", updated=time.time())
        def progress(event):
            with self.lock:
                job.update(event, updated=time.time())
        payload = {key: job[key] for key in ("items", "directory", "options", "preview")}
        payload["pixel_limit"] = self.config.max_pixels
        payload["output_limit"] = self.config.max_output_bytes
        try:
            result = run_process("convert", payload, timeout=self.config.timeout, cancel=job["cancel"], progress=progress)
            with self.lock:
                if job["cancel"].is_set():
                    raise InterruptedError("Conversion cancelled.")
                job.update(result, state="completed", progress=100, detail="Preview ready" if job["preview"] else "Conversion complete", updated=time.time())
        except Exception as error:
            with self.lock:
                cancelled = isinstance(error, InterruptedError)
                job.update(state="cancelled" if cancelled else "failed", detail="Cancelled" if cancelled else "Conversion failed", error=str(error)[:800], updated=time.time())
                # Partial outputs must never be served as completed results.
                self._remove(job["directory"])


class BodyLimitMiddleware:
    def __init__(self, app, maximum):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        try:
            content_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)(scope, receive, send)
        if content_length > self.maximum:
            return await JSONResponse({"detail": "Upload exceeds the configured batch-size limit."}, status_code=413)(scope, receive, send)
        total = 0
        async def limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > self.maximum:
                raise HTTPException(413, "Upload exceeds the configured batch-size limit.")
            return message
        await self.app(scope, limited_receive, send)


def create_app(config=None):
    config = config or Config()
    store = Store(config)
    secret = secrets.token_bytes(32)
    @asynccontextmanager
    async def lifespan(app):
        store.start()
        yield
        store.close()
    async def http_error(request, error):
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)
    app = Starlette(lifespan=lifespan, exception_handlers={HTTPException: http_error})
    app.state.store = store
    app.add_middleware(BodyLimitMiddleware, maximum=config.max_request_bytes)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_hosts)

    def sign(value):
        return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()

    async def session_and_origin(request, call_next):
        cookie = request.cookies.get("ppt_converter_session", "")
        value, separator, signature = cookie.partition(".")
        valid = bool(separator and IDENTIFIER.fullmatch(value) and hmac.compare_digest(sign(value), signature))
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            allowed = {f"http://{host}", f"https://{host}", *config.public_origins}
            if (origin and origin.rstrip("/") not in allowed) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)
            if not valid:
                return JSONResponse({"detail": "Open the app first to start a browser session."}, status_code=403)
        request.state.owner = value if valid else secrets.token_hex(16)
        response = await call_next(request)
        if not valid:
            session_value = request.state.owner
            response.set_cookie("ppt_converter_session", f"{session_value}.{sign(session_value)}", httponly=True, samesite="strict", secure=config.secure_cookie, max_age=86400)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=session_and_origin)

    def route(path, method="GET", status=200):
        def register(function):
            async def endpoint(request):
                try:
                    result = await function(request)
                    return result if isinstance(result, Response) else JSONResponse(result, status_code=status)
                except ValueError as error:
                    raise HTTPException(422, str(error)) from None
            app.add_route(path, endpoint, methods=[method])
            return function
        return register

    @route("/")
    async def index(request):
        return FileResponse(BASE / "static" / "index.html")

    @route("/api/ready")
    async def ready(request):
        from .engine import capabilities
        from .presentation import renderer_status
        return {"ready": True, "renderer": renderer_status(), "formats": capabilities(), "limits": {"file_mb": config.max_file_bytes / 1000000, "batch_mb": config.max_request_bytes / 1000000, "pages": config.max_pages, "pixels": config.max_pixels, "retention_seconds": config.ttl}}

    @route("/api/files", "POST")
    async def upload(request: Request):
        from starlette.datastructures import UploadFile
        async with request.form(max_files=50, max_fields=4, max_part_size=4096) as form:
            incoming = form.getlist("files")
            password = str(form.get("password", ""))[:512]
            if not incoming or any(not isinstance(file, UploadFile) for file in incoming):
                raise HTTPException(422, "Choose one or more files.")
            results, errors = [], []
            for file in incoming:
                asset_id = secrets.token_hex(16)
                directory = config.data_dir / "uploads" / asset_id
                directory.mkdir()
                source = directory / "source.bin"
                try:
                    size = 0
                    with source.open("wb") as output:
                        while chunk := await file.read(1024 * 1024):
                            size += len(chunk)
                            if size > config.max_file_bytes:
                                raise ValueError("File exceeds the configured per-file size limit.")
                            output.write(chunk)
                    if not size:
                        raise ValueError("File is empty.")
                    payload = {"path": str(source), "thumbnail": str(directory / "thumbnail.png"), "password": password, "pixel_limit": config.max_pixels, "page_limit": config.max_pages, "name": file.filename or ""}
                    def inspect(current_payload=payload):
                        with store.inspection_slots:
                            return run_process("inspect", current_payload, timeout=min(config.timeout, 180))
                    info = await asyncio.to_thread(inspect)
                    asset = {"id": asset_id, "path": str(source), "name": (file.filename or "file").replace("\\", "/").split("/")[-1][:180], "size": size, "owner": request.state.owner, "password": password, "created": time.time(), **info}
                    with store.lock:
                        store.assets[asset_id] = asset
                    results.append({key: value for key, value in asset.items() if key not in ("path", "owner", "password", "render_path", "pptx_path")})
                except Exception as error:
                    store._remove(directory)
                    errors.append({"name": (file.filename or "file")[:180], "message": str(error)[:500] if isinstance(error, (ValueError, TimeoutError)) else "Could not inspect this file."})
            return {"files": results, "errors": errors}

    @route("/api/files/{asset_id}/thumbnail")
    async def thumbnail(request):
        asset_id = request.path_params["asset_id"]
        asset = store.asset(asset_id, request.state.owner)
        try:
            page = int(request.query_params.get("page", "0"))
        except ValueError:
            raise HTTPException(422, "Invalid page number.") from None
        if not 0 <= page < asset["pages"]:
            raise HTTPException(422, "Invalid page number.")
        path = Path(asset["path"]).parent / (f"thumbnail-{page}.png" if page else "thumbnail.png")
        if not path.exists():
            def render_thumbnail():
                with store.inspection_slots:
                    if not path.exists():
                        item = {key: asset[key] for key in ("path", "kind", "password", "render_path") if key in asset}
                        run_process("thumbnail", {"item": {**item, "page": page}, "thumbnail": str(path), "pixel_limit": config.max_pixels}, timeout=min(config.timeout, 60))
            await asyncio.to_thread(render_thumbnail)
        return FileResponse(path, media_type="image/png")

    @route("/api/files/{asset_id}", "DELETE")
    async def delete_file(request):
        asset_id = request.path_params["asset_id"]
        with store.lock:
            asset = store.asset(asset_id, request.state.owner)
            if any(job["state"] in ("running", "queued") and any(item["file_id"] == asset_id for item in job["items"]) for job in store.jobs.values()):
                raise HTTPException(409, "This file is in use by a conversion. Cancel it first.")
            store._remove(Path(asset["path"]).parent)
            del store.assets[asset_id]
        return {"deleted": True}

    @route("/api/jobs", "POST", 202)
    async def submit(request):
        body = JobRequest.model_validate(await request.json())
        return store.submit(body, request.state.owner)

    @route("/api/jobs/{job_id}")
    async def status(request):
        job_id = request.path_params["job_id"]
        return store.public_job(store.job(job_id, request.state.owner))

    @route("/api/jobs/{job_id}/cancel", "POST")
    async def cancel(request):
        job_id = request.path_params["job_id"]
        job = store.job(job_id, request.state.owner)
        with store.lock:
            if job["state"] in ("queued", "running"):
                job["cancel"].set()
                job["detail"] = "Cancelling…"
        return store.public_job(job)

    @route("/api/jobs/{job_id}", "DELETE")
    async def delete_job(request):
        job_id = request.path_params["job_id"]
        with store.lock:
            job = store.job(job_id, request.state.owner)
            if job["state"] in ("queued", "running"):
                raise HTTPException(409, "Cancel this job before deleting it.")
            store._remove(job["directory"])
            del store.jobs[job_id]
        return {"deleted": True}

    def completed(job_id, request):
        job = store.job(job_id, request.state.owner)
        if job["state"] != "completed":
            raise HTTPException(409, "Results are not ready.")
        return job

    @route("/api/jobs/{job_id}/outputs/{index:int}")
    async def download(request):
        job_id, index = request.path_params["job_id"], request.path_params["index"]
        job = completed(job_id, request)
        if not 0 <= index < len(job["outputs"]):
            raise HTTPException(404, "Output not found.")
        output = job["outputs"][index]
        return FileResponse(Path(job["directory"]) / output["name"], filename=output["name"])

    @route("/api/jobs/{job_id}/preview")
    async def preview(request):
        job_id = request.path_params["job_id"]
        job = completed(job_id, request)
        return FileResponse(Path(job["directory"]) / "preview.png", media_type="image/png")

    @route("/api/jobs/{job_id}/zip")
    async def zip_download(request):
        job_id = request.path_params["job_id"]
        job = completed(job_id, request)
        return FileResponse(Path(job["directory"]) / "results.zip", filename="converted-files.zip")

    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    return app
