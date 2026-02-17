import time
import uuid
import logging
import os
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from app.producer import publish_event

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Rate Limiter ──────────────────────────────────────────────────────────────
# get_remote_address extracts the client IP for per-IP rate limiting
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="CRM Event Ingestion API",
    description="High-throughput event tracking for CRM analytics",
    version="0.2.0",
)

# Register the rate limit exceeded handler — returns 429 with clear message
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "crm-events")

# ── In-memory metrics (demo only — use Prometheus in production) ──────────────
metrics = {
    "requests_total": 0,
    "requests_accepted": 0,
    "requests_rejected": 0,
    "start_time": time.time(),
}
event_type_counts = defaultdict(int)


# ── Models ────────────────────────────────────────────────────────────────────

class CRMEvent(BaseModel):
    user_id: str = Field(..., description="Unique identifier for the user")
    event_type: str = Field(..., description="Type of event e.g. page_view, email_open")
    session_id: Optional[str] = Field(None, description="Browser/app session identifier")
    properties: Optional[dict] = Field(default_factory=dict, description="Arbitrary event metadata")

    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "user_abc123",
                "event_type": "page_view",
                "session_id": "sess_xyz789",
                "properties": {"page": "/pricing", "referrer": "google"}
            }
        }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "crm-event-api"}


@app.post("/track", status_code=202)
@limiter.limit("100/minute")   # 100 requests per minute per IP
async def track_event(event: CRMEvent, request: Request):
    """
    Rate limited at 100 req/min per IP.
    
    In production you'd tune this per customer tier:
    - Free tier: 60/min
    - Pro tier: 1000/min  
    - Enterprise: unlimited
    """
    metrics["requests_total"] += 1
    event_type_counts[event.event_type] += 1

    enriched_event = {
        **event.dict(),
        "event_id": str(uuid.uuid4()),
        "server_timestamp": time.time(),
        "client_ip": request.client.host,
    }

    logger.info(f"Received event: {event.event_type} from user: {event.user_id}")

    success = publish_event(KAFKA_TOPIC, enriched_event)

    if not success:
        metrics["requests_rejected"] += 1
        raise HTTPException(status_code=503, detail="Event queue unavailable. Please retry.")

    metrics["requests_accepted"] += 1

    return {
        "status": "accepted",
        "event_id": enriched_event["event_id"],
        "message": "Event queued for processing",
    }


@app.get("/metrics")
async def get_metrics():
    """
    Operational metrics — the CTO will appreciate that you built observability in.
    
    In production: use prometheus_client to expose /metrics in Prometheus format
    so Grafana can visualise it. The shape of the data would be the same.
    """
    uptime_seconds = time.time() - metrics["start_time"]
    rps = metrics["requests_total"] / uptime_seconds if uptime_seconds > 0 else 0

    return {
        "uptime_seconds": round(uptime_seconds, 1),
        "requests_total": metrics["requests_total"],
        "requests_accepted": metrics["requests_accepted"],
        "requests_rejected": metrics["requests_rejected"],
        "avg_requests_per_second": round(rps, 2),
        "event_type_breakdown": dict(event_type_counts),
        "kafka_topic": KAFKA_TOPIC,
        "rate_limit": "100 requests/minute per IP",
        "note": "Production would expose this in Prometheus format for Grafana dashboards",
    }