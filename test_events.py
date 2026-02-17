import requests
import time
import random

BASE_URL = "http://localhost:8081"

event_types = [
    "page_view", "email_open", "form_submit",
    "product_view", "churn_signal", "purchase", "login"
]

pages = ["/pricing", "/features", "/dashboard", "/settings", "/cancel"]

print("Sending 20 test events...\n")

for i in range(20):
    event = {
        "user_id": f"user_{random.randint(1, 10):03d}",
        "event_type": random.choice(event_types),
        "session_id": f"sess_{random.randint(100, 999)}",
        "properties": {
            "page": random.choice(pages),
            "duration_seconds": random.randint(5, 300),
        }
    }
    resp = requests.post(f"{BASE_URL}/track", json=event)
    print(f"Event {i+1:02d}: [{event['event_type']}] for {event['user_id']} → {resp.status_code}")
    time.sleep(0.1)

print("\nDone! Check worker logs and Kafka UI at http://localhost:8080")