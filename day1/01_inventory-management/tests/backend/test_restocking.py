"""
Tests for the Restocking feature: recommendations endpoint, submit endpoint,
and the unfiltered restocking-orders list.
"""
import pytest


class TestRestockingRecommendations:
    """Test suite for GET /api/restocking/recommendations."""

    def test_default_budget_returns_items(self, client):
        response = client.get("/api/restocking/recommendations")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_recommendation_shape(self, client):
        response = client.get("/api/restocking/recommendations?budget=10000")
        assert response.status_code == 200
        for rec in response.json():
            for field in (
                "sku", "name", "trend", "demand_gap", "unit_cost",
                "recommended_quantity", "line_cost",
                "current_demand", "forecasted_demand",
            ):
                assert field in rec, f"missing field: {field}"
            assert rec["unit_cost"] > 0
            assert rec["recommended_quantity"] >= 1
            assert rec["line_cost"] == pytest.approx(
                rec["unit_cost"] * rec["recommended_quantity"], rel=1e-3
            )

    def test_total_line_cost_does_not_exceed_budget(self, client):
        budget = 5000
        response = client.get(f"/api/restocking/recommendations?budget={budget}")
        assert response.status_code == 200
        total = sum(r["line_cost"] for r in response.json())
        # Allow tiny float drift but never exceed
        assert total <= budget + 0.01

    def test_zero_budget_returns_empty(self, client):
        response = client.get("/api/restocking/recommendations?budget=0")
        assert response.status_code == 200
        assert response.json() == []

    def test_larger_budget_recommends_at_least_as_much(self, client):
        low = client.get("/api/restocking/recommendations?budget=1000").json()
        high = client.get("/api/restocking/recommendations?budget=20000").json()
        low_total = sum(r["line_cost"] for r in low)
        high_total = sum(r["line_cost"] for r in high)
        assert high_total >= low_total


class TestRestockOrderSubmit:
    """Test suite for POST /api/orders/restock."""

    def test_post_rejects_empty_items(self, client):
        response = client.post("/api/orders/restock", json={"items": []})
        assert response.status_code == 400

    def test_post_rejects_unknown_sku(self, client):
        response = client.post(
            "/api/orders/restock",
            json={"items": [{"sku": "DOES-NOT-EXIST", "quantity": 1}]},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_post_creates_restocking_order(self, client):
        response = client.post(
            "/api/orders/restock",
            json={"items": [{"sku": "PCB-001", "quantity": 3}]},
        )
        assert response.status_code == 200
        order = response.json()
        assert order["source"] == "restocking"
        assert order["status"] == "Processing"
        assert order["customer"] == "Internal Restock"
        assert order["order_number"].startswith("REORD-")
        assert len(order["items"]) == 1
        assert order["items"][0]["sku"] == "PCB-001"
        assert order["items"][0]["quantity"] == 3
        # total_value = unit_cost * quantity
        assert order["total_value"] == pytest.approx(
            order["items"][0]["unit_price"] * 3, rel=1e-3
        )

    def test_post_skips_zero_quantity_lines(self, client):
        response = client.post(
            "/api/orders/restock",
            json={"items": [
                {"sku": "PCB-001", "quantity": 0},
                {"sku": "PSU-501", "quantity": 2},
            ]},
        )
        assert response.status_code == 200
        order = response.json()
        assert len(order["items"]) == 1
        assert order["items"][0]["sku"] == "PSU-501"


class TestRestockingOrdersList:
    """Test suite for GET /api/restocking/orders."""

    def test_path_does_not_collide_with_orders_by_id(self, client):
        # Regression: /api/orders/restocking previously got caught by
        # /api/orders/{order_id}. The endpoint now lives at /api/restocking/orders.
        response = client.get("/api/restocking/orders")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_submitted_order_appears_in_listing(self, client):
        # Submit a fresh order
        submit = client.post(
            "/api/orders/restock",
            json={"items": [{"sku": "PSU-501", "quantity": 1}]},
        )
        assert submit.status_code == 200
        new_id = submit.json()["id"]

        listed = client.get("/api/restocking/orders")
        assert listed.status_code == 200
        ids = [o["id"] for o in listed.json()]
        assert new_id in ids

    def test_listed_orders_are_all_restocking_source(self, client):
        response = client.get("/api/restocking/orders")
        assert response.status_code == 200
        for order in response.json():
            assert order["source"] == "restocking"


class TestRestockOrderIdempotency:
    """Idempotency-Key contract: repeat POSTs with the same key return the
    original order; different keys (or no key) produce fresh orders. Protects
    against double-click submits and any future retry-with-backoff layer."""

    def test_same_idempotency_key_returns_same_order(self, client):
        body = {"items": [{"sku": "PCB-001", "quantity": 2}],
                "idempotency_key": "test-key-same-001"}
        first = client.post("/api/orders/restock", json=body).json()
        second = client.post("/api/orders/restock", json=body).json()
        assert first["id"] == second["id"]
        assert first["order_number"] == second["order_number"]
        assert first["total_value"] == second["total_value"]

    def test_different_idempotency_keys_create_distinct_orders(self, client):
        body_a = {"items": [{"sku": "PCB-001", "quantity": 1}],
                  "idempotency_key": "test-key-distinct-A"}
        body_b = {"items": [{"sku": "PCB-001", "quantity": 1}],
                  "idempotency_key": "test-key-distinct-B"}
        a = client.post("/api/orders/restock", json=body_a).json()
        b = client.post("/api/orders/restock", json=body_b).json()
        assert a["id"] != b["id"]

    def test_no_idempotency_key_creates_new_each_time(self, client):
        body = {"items": [{"sku": "PSU-501", "quantity": 1}]}
        a = client.post("/api/orders/restock", json=body).json()
        b = client.post("/api/orders/restock", json=body).json()
        # No key means each POST creates a fresh order.
        assert a["id"] != b["id"]

    def test_idempotent_replay_does_not_duplicate_in_listing(self, client):
        body = {"items": [{"sku": "PSU-501", "quantity": 1}],
                "idempotency_key": "test-key-no-dup-001"}
        client.post("/api/orders/restock", json=body)
        before = len(client.get("/api/restocking/orders").json())
        client.post("/api/orders/restock", json=body)  # replay
        client.post("/api/orders/restock", json=body)  # replay
        after = len(client.get("/api/restocking/orders").json())
        assert after == before
