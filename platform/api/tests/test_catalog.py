"""Tests for the catalog router."""

import pytest

from ..routers.catalog import CATALOG


def test_list_catalog(client):
    response = client.get("/v1/catalog")
    assert response.status_code == 200
    ids = {t["template_id"] for t in response.json()}
    assert ids == set(CATALOG)
    assert "eks-microservice" in ids


def test_get_template(client):
    response = client.get("/v1/catalog/eks-microservice")
    assert response.status_code == 200
    body = response.json()
    assert body["template_id"] == "eks-microservice"
    assert body["parameters"][0]["name"] == "min_replicas"


def test_get_unknown_template_404(client):
    response = client.get("/v1/catalog/nope")
    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


@pytest.mark.parametrize("path", ["/v1/catalog", "/v1/catalog/eks-microservice"])
class TestCatalogAuth:
    def test_unauthenticated_401(self, make_client, path):
        assert make_client(None).get(path).status_code == 401

    @pytest.mark.parametrize("scopes", [{"idp:provision"}, {"idp:destroy"}, set()])
    def test_missing_read_scope_403(self, make_client, path, scopes):
        response = make_client(scopes).get(path)
        assert response.status_code == 403
        assert response.json()["detail"] == "Required scope: idp:read"

    def test_read_scope_only_is_enough(self, make_client, path):
        assert make_client({"idp:read"}).get(path).status_code == 200
