"""测试共用：裸 ASGI 请求助手（项目无 httpx/TestClient，沿用 test_analysis 的直调模式）。"""
import json


class CookieJar:
    """从 set-cookie 响应头收集 Cookie，并在后续请求中回传。"""

    def __init__(self):
        self.cookies = {}

    def store(self, header_value: bytes) -> None:
        first = header_value.decode("latin-1").split(";", 1)[0]
        if "=" in first:
            key, value = first.split("=", 1)
            self.cookies[key] = value

    @property
    def header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())


async def request(app, method: str, path: str, body=None, cookies: CookieJar | None = None,
                  client_host: str = "test", raw_body: bytes | None = None,
                  extra_headers: list | None = None):
    """直调 ASGI 应用，返回 (status, json/body, raw_headers)。"""
    messages: list = []
    payload = raw_body if raw_body is not None else (
        json.dumps(body).encode() if body is not None else b"")
    headers = [(b"content-type", b"application/json")]
    if cookies and cookies.header:
        headers.append((b"cookie", cookies.header.encode()))
    headers.extend(extra_headers or [])
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "headers": headers,
             "client": (client_host, 1), "server": ("test", 80)}

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    status = messages[0]["status"]
    raw_headers = messages[0].get("headers", [])
    body_bytes = b"".join(m.get("body", b"") for m in messages)
    for key, value in raw_headers:
        if key == b"set-cookie" and cookies is not None:
            cookies.store(value)
    try:
        parsed = json.loads(body_bytes) if body_bytes else {}
    except ValueError:
        parsed = body_bytes
    return status, parsed, raw_headers
