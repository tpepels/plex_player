"""Minimal HTTP helpers built on the Python standard library.

This keeps resident memory lower than a full third-party HTTP stack on
small devices such as Raspberry Pi Zero-class boards.
"""

from dataclasses import dataclass
import json
from typing import Mapping, Optional
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


def _merge_query_params(url: str, params: Optional[Mapping[str, object]]) -> str:
    if not params:
        return url

    split = urlsplit(url)
    merged = parse_qsl(split.query, keep_blank_values=True)
    merged.extend((key, value) for key, value in params.items() if value is not None)
    return urlunsplit(
        (
            split.scheme,
            split.netloc,
            split.path,
            urlencode(merged),
            split.fragment,
        )
    )


class HttpStatusError(RuntimeError):
    """Raised when an HTTP response returns a 4xx/5xx status code."""


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    content: bytes
    headers: object

    @property
    def text(self) -> str:
        charset = getattr(self.headers, "get_content_charset", lambda: None)() or "utf-8"
        return self.content.decode(charset, errors="replace")

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HttpStatusError(f"HTTP {self.status_code}: {self.text[:200]}")


def get(
    url: str,
    *,
    params: Optional[Mapping[str, object]] = None,
    headers: Optional[Mapping[str, str]] = None,
    timeout: int | float = 10,
) -> HttpResponse:
    request = Request(
        _merge_query_params(url, params),
        headers=dict(headers or {}),
        method="GET",
    )
    try:
        with urlopen(request, timeout=float(timeout)) as response:
            return HttpResponse(
                status_code=getattr(response, "status", response.getcode()),
                content=response.read(),
                headers=response.headers,
            )
    except HTTPError as exc:
        error_response = HttpResponse(
            status_code=exc.code,
            content=exc.read(),
            headers=exc.headers,
        )
        raise HttpStatusError(
            f"HTTP {error_response.status_code}: {error_response.text[:200]}"
        ) from exc
