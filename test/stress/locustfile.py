"""Locust stress test for the Museums REST API.

Two user classes with different weights model real-world traffic:
  - ReadUser (90 %)  — consumers querying museums, cities, regression
  - IngestUser (10 %) — admins triggering the occasional data refresh

Run locally:
    uv run locust --host http://localhost:8000

Headless CI run (see nightly.yml / stress-test.yml for full flags):
    uv run locust --headless --host http://localhost:8000 \
        --users 50 --spawn-rate 10 --run-time 2m \
        --locustfile tests/stress/locustfile.py
"""

import logging

from locust import HttpUser, between, events, task

logger = logging.getLogger("museums.stress")


# ── Event hooks ───────────────────────────────────────────────────────────────


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Verify the API has data before the swarm starts hitting read endpoints."""
    with environment.runner.client.get(
        "/museums", catch_response=True, name="[preflight] GET /museums"
    ) as resp:
        if resp.status_code == 200 and resp.json():
            logger.info("Preflight OK — %d museums in DB", len(resp.json()))
        else:
            logger.warning(
                "No museums found — read endpoints may return empty results. "
                "Run POST /ingest before stress-testing."
            )


# ── User classes ──────────────────────────────────────────────────────────────


class ReadUser(HttpUser):
    """Simulates a typical API consumer: reads museums, cities, and regression.

    Task weight distribution (roughly matching real-world API usage):
      5x  GET /museums    — most common
      3x  GET /cities     — frequent
      2x  GET /regression — less frequent, more expensive
    """

    weight = 9  # 90 % of virtual users
    wait_time = between(0.2, 1.5)

    @task(5)
    def list_museums(self):
        with self.client.get("/museums", catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Expected 200, got {resp.status_code}")

    @task(3)
    def list_cities(self):
        with self.client.get("/cities", catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Expected 200, got {resp.status_code}")

    @task(2)
    def regression(self):
        with self.client.get("/regression", catch_response=True) as resp:
            if resp.status_code == 200:
                body = resp.json()
                # Validate response shape, not just status code
                if "r_squared" not in body:
                    resp.failure("Missing r_squared in regression response")
                else:
                    resp.success()
            elif resp.status_code == 422:
                # No data yet — mark as success so it doesn't inflate error rate
                resp.success()
            else:
                resp.failure(f"Expected 200 or 422, got {resp.status_code}")


class IngestUser(HttpUser):
    """Simulates a rare admin triggering data refresh.

    Intentionally slow wait time — ingest calls external APIs (Wikipedia,
    Wikidata) and should not be triggered at high concurrency.
    """

    weight = 1  # 10 % of virtual users
    wait_time = between(30, 90)  # ingest is an expensive, infrequent operation

    @task
    def ingest(self):
        with self.client.post(
            "/ingest",
            catch_response=True,
            # Long timeout — ingest calls two external APIs
            timeout=120,
        ) as resp:
            if resp.status_code == 200:
                body = resp.json()
                if body.get("ingested_museums", 0) > 0:
                    resp.success()
                else:
                    resp.failure("Ingest returned 0 museums")
            else:
                resp.failure(f"Ingest failed with status {resp.status_code}")
