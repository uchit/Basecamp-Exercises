"""
Regression tests: the /api/reports/* endpoints must honour the global
FilterBar parameters (warehouse, category, status, month). Previously they
ignored every filter, which is one of the planted bugs the Expert Challenge
asks us to fix.
"""
import pytest


class TestQuarterlyReportFilters:

    def test_returns_quarters_unfiltered(self, client):
        response = client.get("/api/reports/quarterly")
        assert response.status_code == 200
        data = response.json()
        assert len(data) > 0
        # Every quarter should be a recognised string
        for q in data:
            assert q["quarter"].startswith("Q")

    def test_warehouse_filter_changes_results(self, client):
        baseline = client.get("/api/reports/quarterly").json()
        tokyo = client.get("/api/reports/quarterly?warehouse=Tokyo").json()
        baseline_total = sum(q["total_orders"] for q in baseline)
        tokyo_total = sum(q["total_orders"] for q in tokyo)
        # Tokyo should be a strict subset
        assert tokyo_total < baseline_total
        assert tokyo_total > 0

    def test_status_delivered_yields_100_percent_fulfillment(self, client):
        response = client.get("/api/reports/quarterly?status=Delivered")
        assert response.status_code == 200
        for q in response.json():
            if q["total_orders"] > 0:
                assert q["fulfillment_rate"] == pytest.approx(100.0)

    def test_all_sentinel_is_treated_as_no_filter(self, client):
        baseline = client.get("/api/reports/quarterly").json()
        all_sentinel = client.get("/api/reports/quarterly?warehouse=all&category=all&status=all").json()
        assert all_sentinel == baseline


class TestMonthlyTrendsFilters:

    def test_warehouse_filter_changes_results(self, client):
        baseline = client.get("/api/reports/monthly-trends").json()
        tokyo = client.get("/api/reports/monthly-trends?warehouse=Tokyo").json()
        baseline_total = sum(m["revenue"] for m in baseline)
        tokyo_total = sum(m["revenue"] for m in tokyo)
        assert tokyo_total < baseline_total

    def test_month_filter_returns_only_that_month(self, client):
        response = client.get("/api/reports/monthly-trends?month=2025-03")
        assert response.status_code == 200
        data = response.json()
        # 2025-03 should be the only key present
        assert {m["month"] for m in data} == {"2025-03"}
