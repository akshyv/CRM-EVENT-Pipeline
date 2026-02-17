"""
Locust load test — simulates CRM platform traffic patterns.

Scenario: Mixed traffic from a marketing CRM
- Most traffic is page views and email opens (normal activity)
- Occasional purchases and churn signals (high-value events)
- Some users doing rapid actions (power users / potential abuse)

Run with:
  locust -f loadtest.py --host http://localhost
"""

import time
import random
import uuid
from locust import HttpUser, task, between


# ── Realistic CRM event data ──────────────────────────────────────────────────

PAGES = ["/dashboard", "/campaigns", "/contacts", "/pricing", "/settings",
         "/reports", "/integrations", "/cancel", "/upgrade"]

EMAIL_CAMPAIGNS = ["welcome_series", "re_engagement", "product_update",
                   "promo_black_friday", "churn_prevention"]

PRODUCTS = ["starter_plan", "pro_plan", "enterprise_plan", "addon_analytics",
            "addon_ai_insights"]


def random_user_id():
    return f"user_{random.randint(1, 500):04d}"


def random_session():
    return str(uuid.uuid4())[:8]


# ── User behavior classes ──────────────────────────────────────────────────────

class RegularUser(HttpUser):
    """
    Represents a typical CRM platform user — browsing, reading emails.
    80% of your traffic looks like this.
    """
    weight = 8   # 8x more common than PowerUser
    wait_time = between(0.5, 2.0)  # realistic human think time

    def on_start(self):
        self.user_id = random_user_id()
        self.session_id = random_session()

    @task(5)   # weight: most common action
    def page_view(self):
        self.client.post("/track", json={
            "user_id": self.user_id,
            "event_type": "page_view",
            "session_id": self.session_id,
            "properties": {
                "page": random.choice(PAGES),
                "duration_seconds": random.randint(10, 120),
            }
        })

    @task(3)
    def email_open(self):
        self.client.post("/track", json={
            "user_id": self.user_id,
            "event_type": "email_open",
            "session_id": self.session_id,
            "properties": {
                "campaign": random.choice(EMAIL_CAMPAIGNS),
                "email_client": random.choice(["gmail", "outlook", "apple_mail"]),
            }
        })

    @task(1)
    def product_view(self):
        self.client.post("/track", json={
            "user_id": self.user_id,
            "event_type": "product_view",
            "session_id": self.session_id,
            "properties": {
                "product": random.choice(PRODUCTS),
            }
        })


class PowerUser(HttpUser):
    """
    Active users doing many actions quickly.
    Also models potential API abuse scenarios — rate limiter should kick in.
    """
    weight = 2
    wait_time = between(0.05, 0.2)  # very fast — will trigger rate limits

    def on_start(self):
        self.user_id = random_user_id()
        self.session_id = random_session()

    @task(3)
    def rapid_page_view(self):
        self.client.post("/track", json={
            "user_id": self.user_id,
            "event_type": "page_view",
            "session_id": self.session_id,
            "properties": {"page": random.choice(PAGES)},
        })

    @task(2)
    def form_submit(self):
        self.client.post("/track", json={
            "user_id": self.user_id,
            "event_type": "form_submit",
            "session_id": self.session_id,
            "properties": {"form": "contact_sales"},
        })

    @task(1)
    def purchase(self):
        self.client.post("/track", json={
            "user_id": self.user_id,
            "event_type": "purchase",
            "session_id": self.session_id,
            "properties": {
                "product": random.choice(PRODUCTS),
                "amount_usd": random.randint(49, 999),
            }
        })


class ChurnRiskUser(HttpUser):
    """
    Users showing churn behaviour — low engagement, hitting cancel pages.
    Important for CRM churn prediction models.
    """
    weight = 1
    wait_time = between(1.0, 5.0)

    def on_start(self):
        self.user_id = random_user_id()
        self.session_id = random_session()

    @task(2)
    def cancel_page_view(self):
        self.client.post("/track", json={
            "user_id": self.user_id,
            "event_type": "page_view",
            "session_id": self.session_id,
            "properties": {"page": "/cancel", "referrer": "/settings"},
        })

    @task(1)
    def churn_signal(self):
        self.client.post("/track", json={
            "user_id": self.user_id,
            "event_type": "churn_signal",
            "session_id": self.session_id,
            "properties": {
                "days_inactive": random.randint(14, 90),
                "last_action": "viewed_cancel_page",
                "subscription_days_remaining": random.randint(1, 30),
            }
        })