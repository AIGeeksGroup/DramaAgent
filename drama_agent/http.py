"""Small bounded HTTP client; API keys come from the environment only."""
import json
import os
from pathlib import Path
import shutil
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlparse


def api_key(env_name):
    value = os.environ.get(env_name)
    if not value:
        raise ValueError(f"Set {env_name} before using this provider")
    return value


def request_json(url, key, payload=None, headers=None, timeout=120):
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("API URL must use HTTP(S)")
    request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}", **(headers or {})})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        # Do not echo API response bodies, which can include prompt data and signed URLs.
        raise RuntimeError(f"Provider returned HTTP {exc.code} for {urlparse(url).path}") from None


def download(url, output):
    if urlparse(url).scheme not in ("https", "http"):
        raise ValueError("Provider download must use HTTP(S)")
    output = Path(output)
    temporary = output.with_suffix(output.suffix + ".download")
    with urlopen(url, timeout=180) as response, temporary.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    temporary.replace(output)
    return str(output)
