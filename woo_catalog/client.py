"""Small WooCommerce REST API adapter with safe error messages."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import requests

from .config import WooConfig


class WooClientError(RuntimeError):
    """A WooCommerce request failed; message intentionally excludes credentials."""

    def __init__(
        self,
        message: str,
        *,
        request_may_have_been_sent: bool = False,
        http_status: int | None = None,
        woo_code: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.request_may_have_been_sent = request_may_have_been_sent
        self.http_status = http_status
        self.woo_code = woo_code
        self.retry_after_seconds = retry_after_seconds

    def public_details(self) -> dict[str, Any]:
        return {
            "http_status": self.http_status,
            "woo_code": self.woo_code,
            "retry_after_seconds": self.retry_after_seconds,
            "request_may_have_been_sent": self.request_may_have_been_sent,
        }


@dataclass(frozen=True)
class Page:
    items: list[dict[str, Any]]
    page: int
    total_pages: int
    total_items: int
    next_url: str | None = None


class WooClient:
    def __init__(
        self,
        config: WooConfig,
        session: requests.Session | Any | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.session = session or requests.Session()
        self._sleep = sleep

    def list_all(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        page = 1
        result: list[dict[str, Any]] = []
        total_pages = 1
        next_url: str | None = None
        while True:
            if next_url is None:
                page_params = {**(params or {}), "per_page": 100, "page": page}
                response, payload = self._request("GET", path, params=page_params)
            else:
                response, payload = self._request_url("GET", next_url)
            if not isinstance(payload, list):
                raise WooClientError(f"WooCommerce returned a non-list response for {path}")
            result.extend(payload)
            try:
                total_pages = int(response.headers.get("X-WP-TotalPages", "1"))
            except (AttributeError, ValueError) as error:
                raise WooClientError(f"WooCommerce returned invalid pagination headers for {path}") from error
            next_url = self._next_link(response)
            if next_url is not None and not self._is_api_url(next_url):
                next_url = None
            if next_url is None and page >= total_pages:
                break
            page += 1
        return result

    def list_page(
        self,
        path: str,
        *,
        page: int,
        per_page: int,
        params: dict[str, Any] | None = None,
    ) -> Page:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")
        response, payload = self._request(
            "GET",
            path,
            params={**(params or {}), "page": page, "per_page": per_page},
        )
        if not isinstance(payload, list):
            raise WooClientError(f"WooCommerce returned a non-list response for {path}")
        try:
            total_pages = int(response.headers.get("X-WP-TotalPages", "1"))
            total_items = int(response.headers.get("X-WP-Total", str(len(payload))))
        except (AttributeError, ValueError) as error:
            raise WooClientError(f"WooCommerce returned invalid pagination headers for {path}") from error
        next_url = self._next_link(response)
        return Page(
            items=payload,
            page=page,
            total_pages=total_pages,
            total_items=total_items,
            next_url=next_url if next_url and self._is_api_url(next_url) else None,
        )

    def find_product(self, sku: str) -> dict[str, Any] | None:
        rows = self.list_all("products", params={"sku": sku})
        return rows[0] if rows else None

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        _, payload = self._request("GET", path, params=params)
        if not isinstance(payload, dict):
            raise WooClientError(f"WooCommerce returned a non-object response for {path}")
        return payload

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._write("POST", path, payload)

    def put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._write("PUT", path, payload)

    def delete(self, path: str, *, force: bool = False) -> dict[str, Any]:
        _, result = self._request("DELETE", path, params={"force": force})
        if not isinstance(result, dict):
            raise WooClientError(
                f"WooCommerce returned a non-object response for {path}",
                request_may_have_been_sent=True,
            )
        return result

    def post_batch(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(path, payload)

    def diagnose(self) -> dict[str, Any]:
        """Probe documented read-only routes without attempting a write."""

        checks: list[dict[str, Any]] = []
        root_payload: dict[str, Any] | None = None
        for name, method, target, root in (
            ("wordpress_rest_index", "GET", "wp-json/", True),
            ("catalog_read", "GET", "products", False),
            ("settings_read", "GET", "settings", False),
            ("product_batch", "OPTIONS", "products/batch", False),
        ):
            try:
                if root:
                    _, payload = self._request_root(method, target)
                elif name == "catalog_read":
                    _, payload = self._request(method, target, params={"per_page": 1})
                else:
                    _, payload = self._request(method, target)
                if name == "wordpress_rest_index" and isinstance(payload, dict):
                    root_payload = payload
                checks.append({"id": name, "status": "passed"})
            except WooClientError as error:
                checks.append({
                    "id": name,
                    "status": "failed",
                    "error": error.public_details(),
                })
        by_id = {item["id"]: item for item in checks}
        namespaces = root_payload.get("namespaces", []) if root_payload else []
        return {
            "store": {
                "origin": self.config.store_identity,
                "api_base_url": self.config.api_base_url,
                "namespaces": namespaces if isinstance(namespaces, list) else [],
            },
            "checks": checks,
            "capabilities": {
                "catalog_read": by_id["catalog_read"]["status"] == "passed",
                "settings_read": by_id["settings_read"]["status"] == "passed",
                "product_batch": by_id["product_batch"]["status"] == "passed",
                "write_permission": "not_probed",
            },
        }

    def _write(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        _, result = self._request(method, path, json=payload)
        if not isinstance(result, dict):
            raise WooClientError(
                f"WooCommerce returned a non-object response for {path}",
                request_may_have_been_sent=True,
            )
        return result

    def _request(self, method: str, path: str, **kwargs: Any) -> tuple[Any, Any]:
        return self._request_url(method, f"{self.config.api_base_url}/{path.lstrip('/')}", **kwargs)

    def _request_root(self, method: str, path: str, **kwargs: Any) -> tuple[Any, Any]:
        return self._request_url(method, urljoin(f"{self.config.base_url.rstrip('/')}/", path), **kwargs)

    def _request_url(self, method: str, url: str, **kwargs: Any) -> tuple[Any, Any]:
        attempts = self.config.read_retry_attempts if method in {"GET", "OPTIONS"} else 1
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    auth=(self.config.consumer_key, self.config.consumer_secret),
                    timeout=self.config.timeout_seconds,
                    **kwargs,
                )
                response.raise_for_status()
                return response, response.json()
            except requests.RequestException as error:
                wrapped = self._request_error(method, error)
                if attempt < attempts and self._retryable_read(method, wrapped):
                    self._sleep(self._retry_delay(wrapped, attempt))
                    continue
                raise wrapped from error
            except ValueError as error:
                raise WooClientError(
                    "WooCommerce returned invalid JSON",
                    request_may_have_been_sent=method not in {"GET", "OPTIONS"},
                ) from error
        raise AssertionError("unreachable")

    def _request_error(self, method: str, error: requests.RequestException) -> WooClientError:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        payload: Any = None
        try:
            payload = response.json() if response is not None else None
        except ValueError:
            payload = None
        woo_code = payload.get("code") if isinstance(payload, dict) and isinstance(payload.get("code"), str) else None
        retry_after = self._retry_after(getattr(response, "headers", None))
        prefix = f"WooCommerce request failed ({status})" if status else "WooCommerce request failed"
        return WooClientError(
            prefix,
            request_may_have_been_sent=(
                method not in {"GET", "OPTIONS"} and (response is None or (status is not None and status >= 500))
            ),
            http_status=status,
            woo_code=woo_code,
            retry_after_seconds=retry_after,
        )

    @staticmethod
    def _retry_after(headers: Any) -> float | None:
        try:
            value = float((headers or {}).get("Retry-After", ""))
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    @staticmethod
    def _retryable_read(method: str, error: WooClientError) -> bool:
        return method in {"GET", "OPTIONS"} and (error.http_status is None or error.http_status in {429, 502, 503, 504})

    def _retry_delay(self, error: WooClientError, attempt: int) -> float:
        if error.retry_after_seconds is not None:
            return min(error.retry_after_seconds, 30)
        return min(self.config.read_retry_backoff_seconds * (2 ** (attempt - 1)), 30)

    @staticmethod
    def _next_link(response: Any) -> str | None:
        links = getattr(response, "links", None)
        if isinstance(links, dict) and links.get("next"):
            next_link = links["next"]
            if isinstance(next_link, dict):
                return next_link.get("url")
            if isinstance(next_link, list) and next_link and isinstance(next_link[0], dict):
                return next_link[0].get("url")
        return None

    def _is_api_url(self, url: str) -> bool:
        candidate = urlparse(url)
        expected = urlparse(self.config.api_base_url)
        return (
            candidate.scheme == expected.scheme
            and candidate.netloc == expected.netloc
            and candidate.path.startswith(expected.path)
        )
