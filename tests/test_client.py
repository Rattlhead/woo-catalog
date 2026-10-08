from dataclasses import dataclass

import pytest

from woo_catalog.client import WooClient, WooClientError
from woo_catalog.config import WooConfig


@dataclass
class FakeResponse:
    payload: object
    status_code: int = 200
    headers: dict | None = None
    text: str = ""

    def json(self):
        return self.payload


    def raise_for_status(self):
        if self.status_code >= 400:
            from requests import HTTPError

            raise HTTPError(response=self)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def config():
    return WooConfig(
        base_url="https://shop.example",
        consumer_key="ck_secret",
        consumer_secret="cs_secret",
        timeout_seconds=10,
    )


def test_list_all_follows_wordpress_pagination_headers():
    session = FakeSession(
        [
            FakeResponse([{"id": 1}], headers={"X-WP-TotalPages": "2"}),
            FakeResponse([{"id": 2}], headers={"X-WP-TotalPages": "2"}),
        ]
    )

    result = WooClient(config(), session=session).list_all("products")

    assert result == [{"id": 1}, {"id": 2}]
    assert [call[2]["params"]["page"] for call in session.calls] == [1, 2]
    assert session.calls[0][2]["params"]["per_page"] == 100


def test_list_all_follows_a_same_origin_next_link_when_headers_are_wrong():
    first = FakeResponse([{"id": 1}], headers={"X-WP-TotalPages": "1"})
    first.links = {"next": {"url": "https://shop.example/wp-json/wc/v3/products?page=2"}}
    second = FakeResponse([{"id": 2}], headers={"X-WP-TotalPages": "1"})
    session = FakeSession([first, second])

    result = WooClient(config(), session=session).list_all("products")

    assert result == [{"id": 1}, {"id": 2}]
    assert session.calls[1][1].endswith("products?page=2")


def test_list_page_returns_bounded_items_and_cursor_metadata():
    session = FakeSession(
        [FakeResponse([{"id": 3}], headers={"X-WP-TotalPages": "4", "X-WP-Total": "31"})]
    )

    page = WooClient(config(), session=session).list_page("products", page=2, per_page=10, params={"status": "publish"})

    assert page.items == [{"id": 3}]
    assert page.page == 2
    assert page.total_pages == 4
    assert page.total_items == 31
    assert session.calls[0][2]["params"] == {"status": "publish", "page": 2, "per_page": 10}


def test_client_error_does_not_leak_api_keys():
    session = FakeSession([FakeResponse({"code": "rest_forbidden"}, 403, text="no")])

    with pytest.raises(WooClientError) as caught:
        WooClient(config(), session=session).get("products/1")

    assert "ck_secret" not in str(caught.value)
    assert "cs_secret" not in str(caught.value)


def test_delete_sends_force_parameter_without_putting_it_in_the_url():
    session = FakeSession([FakeResponse({"id": 1})])

    WooClient(config(), session=session).delete("products/1", force=False)

    assert session.calls[0][0] == "DELETE"
    assert session.calls[0][2]["params"] == {"force": False}


def test_mutation_server_error_is_marked_uncertain():
    session = FakeSession([FakeResponse({"code": "server_error"}, 500)])

    with pytest.raises(WooClientError) as caught:
        WooClient(config(), session=session).put("products/1", {"name": "New"})

    assert caught.value.request_may_have_been_sent is True


def test_successful_mutation_with_unexpected_body_is_marked_uncertain():
    session = FakeSession([FakeResponse([])])

    with pytest.raises(WooClientError) as caught:
        WooClient(config(), session=session).post("products", {"sku": "A"})

    assert caught.value.request_may_have_been_sent is True


def test_get_retries_a_transient_woocommerce_error_and_exposes_safe_details():
    session = FakeSession([
        FakeResponse({"code": "woocommerce_rest_cannot_view"}, 503, headers={"Retry-After": "2"}),
        FakeResponse({"id": 1}, headers={}),
    ])
    waits = []

    result = WooClient(config(), session=session, sleep=waits.append).get("products/1")

    assert result == {"id": 1}
    assert waits == [2]
    assert len(session.calls) == 2


def test_put_does_not_retry_a_transient_error():
    session = FakeSession([FakeResponse({"code": "server_error"}, 503, headers={})])

    with pytest.raises(WooClientError) as caught:
        WooClient(config(), session=session).put("products/1", {"name": "New"})

    assert len(session.calls) == 1
    assert caught.value.woo_code == "server_error"
    assert caught.value.public_details()["http_status"] == 503


def test_diagnose_probes_only_documented_read_only_routes():
    session = FakeSession([
        FakeResponse({"namespaces": ["wc/v3"]}, headers={}),
        FakeResponse([], headers={}),
        FakeResponse([], headers={}),
        FakeResponse({"methods": ["POST"]}, headers={}),
    ])

    report = WooClient(config(), session=session).diagnose()

    assert report["store"]["namespaces"] == ["wc/v3"]
    assert report["capabilities"] == {
        "catalog_read": True,
        "settings_read": True,
        "product_batch": True,
        "write_permission": "not_probed",
    }
    assert [call[0] for call in session.calls] == ["GET", "GET", "GET", "OPTIONS"]
    assert session.calls[0][1] == "https://shop.example/wp-json/"
