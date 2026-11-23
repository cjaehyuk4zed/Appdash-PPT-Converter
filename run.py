import os

import uvicorn

from converter.server import create_app

if __name__ == "__main__":
    # One ASGI process owns the in-memory queue and browser sessions.
    uvicorn.run(create_app(), host=os.environ.get("CONVERTER_HOST", "127.0.0.1"), port=int(os.environ.get("CONVERTER_PORT", "8766")), access_log=False, proxy_headers=True, forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))
