# CRM Event Pipeline — Production-Scale Event Ingestion

A high-throughput event ingestion system demonstrating scalable architecture patterns for CRM platforms. Built to showcase traffic management, horizontal scaling, and async processing.

## 🎯 Problem Statement

Modern CRM platforms (like HubSpot, Salesforce, Braze) track millions of user interactions daily:
- Page views, email opens, form submissions
- Purchase events, churn signals
- Real-time behavioral data feeding ML models

**Challenge:** API must respond instantly regardless of downstream processing time.

**Solution:** Event-driven architecture with Kafka as a durable buffer.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│                  INGESTION LAYER                    │
│                                                     │
│  Client → Nginx (Port 8081)                        │
│           • Rate limiting: 20 req/s per IP         │
│           • Round-robin load balancing             │
│           • Connection pooling                     │
└─────────────────┬───────────────────────────────────┘
                  │
     ┌────────────┼────────────┐
     ▼            ▼            ▼
┌─────────┐  ┌─────────┐  ┌─────────┐
│ API #1  │  │ API #2  │  │ API #3  │
│ FastAPI │  │ FastAPI │  │ FastAPI │
└────┬────┘  └────┬────┘  └────┬────┘
     │            │            │
     └────────────┼────────────┘
                  │ publish
                  ▼
         ┌─────────────────┐
         │   Apache Kafka  │
         │  Topic:         │
         │  crm-events     │
         │                 │
         │  • Durable      │
         │  • Replayable   │
         │  • Partitioned  │
         └────────┬────────┘
                  │ consume
         ┌────────┴────────┐
         ▼                 ▼
    ┌─────────┐       ┌─────────┐
    │Worker #1│       │Worker #2│
    │Consumer │       │Consumer │
    │Group:   │       │Group:   │
    │crm-     │       │crm-     │
    │events   │       │events   │
    └─────────┘       └─────────┘
```

---

## 🔑 Key Design Decisions

### Why Kafka instead of direct database writes?

**The API must respond in <10ms** regardless of downstream complexity.

- ❌ **Synchronous processing:** API waits for DB write, ML model update, email trigger → slow, couples systems
- ✅ **Event-driven:** API just publishes to Kafka (~1ms), responds 202 Accepted → fast, decoupled

### Why Nginx load balancer?

**Horizontal scaling > vertical scaling**

- Single beefy server has a ceiling
- 3 cheap servers behind Nginx = 3x throughput
- Can scale to 10, 50, 100 instances as traffic grows
- Also provides rate limiting, SSL termination, circuit breaking

### Why consumer groups?

**Kafka's partition magic**

- Topic has multiple partitions (we use 2)
- Each worker in a consumer group gets assigned partitions
- 2 workers = each processes ~50% of events
- Add a 3rd worker = automatic rebalancing to ~33% each
- No coordination needed, no duplicate processing

---

## 📊 Performance Characteristics

Tested with Locust at 200 concurrent users:

- **Throughput:** ~150 requests/second sustained
- **Latency (p95):** <50ms for `/track` endpoint
- **Failure rate:** 0% under normal load
- **Kafka lag:** Workers keep up in real-time

Rate limiting triggers at:
- Nginx: 20 req/s per IP (burst: 50)
- FastAPI: 100 req/min per IP

---

## 🚀 Quick Start

### Prerequisites
- Docker Desktop running
- Python 3.11+

### Run the system
```bash
docker compose up --build
```

Wait ~60 seconds for Kafka to be ready.

### Verify it's alive
```bash
# Health check
curl http://localhost:8081/health

# OR use PowerShell
Invoke-WebRequest -Uri "http://localhost:8081/health"

# Send test events
python test_events.py
```

### Access dashboards
- **API Docs:** http://localhost:8081/docs (Swagger UI)
- **Kafka UI:** http://localhost:8080
- **Metrics:** http://localhost:8081/metrics

### Run load test
```bash
pip install locust
locust -f loadtest.py --host http://localhost:8081
```

Open http://localhost:8089, set 200 users, click "Start swarming".

---

## 📁 Project Structure

```
crm-event-pipeline/
├── app/
│   ├── main.py          # FastAPI app, rate limiting, metrics
│   └── producer.py      # Kafka producer singleton
├── worker/
│   └── consumer.py      # Kafka consumer with retry logic
├── nginx/
│   └── nginx.conf       # Load balancer config
├── docker-compose.yml   # Full stack orchestration
├── Dockerfile.api       # API container build
├── Dockerfile.worker    # Worker container build
├── loadtest.py          # Locust traffic simulation
├── test_events.py       # Quick manual test script
└── requirements.txt     # Python dependencies
```

---

## 🎓 What This Demonstrates

### Traffic Management
- **Rate limiting** at two layers (Nginx + application)
- **Backpressure handling** via Kafka buffering
- **Load distribution** across multiple API instances

### Scalability Patterns
- **Horizontal scaling** (not just bigger servers)
- **Async processing** (API never blocks)
- **Consumer groups** for parallel workers

### Production Thinking
- **Observability:** /metrics endpoint (would be Prometheus in prod)
- **Graceful degradation:** 503 when Kafka down (don't silently drop data)
- **Semantic HTTP:** 202 Accepted (not 200 OK) for async work
- **Containerization:** One command to run entire stack

---

## 🔮 Production Enhancements (Not Implemented)

If this were a real CRM platform, next steps would be:

### Observability
- Prometheus + Grafana for metrics visualization
- Structured logging (JSON) → Elasticsearch/Datadog
- Distributed tracing (OpenTelemetry)

### Reliability
- Multi-region Kafka cluster (3+ brokers, replication factor 3)
- Dead letter queue for poison messages
- Circuit breakers (if Kafka down, degrade gracefully)

### Data Pipeline
- Schema registry (Avro/Protobuf) for event versioning
- Stream processing (Kafka Streams / Flink) for real-time aggregations
- Data lake (S3/BigQuery) for cold storage and analytics

### Security
- OAuth2 / API keys for authentication
- TLS everywhere (Nginx SSL, Kafka mTLS)
- Field-level encryption for PII



## 🐛 Troubleshooting

### Kafka slow to start
```bash
# Kafka can take 60 seconds to fully initialize — this is normal
docker compose logs kafka --tail=50
```

### Workers not processing
```bash
# Check worker logs for connection errors
docker compose logs worker -f

# Verify topic exists with correct partitions
docker exec kafka kafka-topics --list --bootstrap-server localhost:9092
docker exec kafka kafka-topics --describe --topic crm-events --bootstrap-server localhost:9092
```

### Port conflicts
```bash
# If port 8081 is in use, change it in docker-compose.yml under nginx:
ports:
  - "8082:80"  # or any other free port

# Then update test_events.py and loadtest.py BASE_URL accordingly
```

### Nginx returns 502
```bash
# API instances aren't ready yet
docker compose logs api

# Give it 30 more seconds, Kafka might still be initializing
```

### Cannot connect to Kafka
```bash
# This is normal for 20-30 seconds after startup
# The worker has retry logic built in

# If it persists after 2 minutes:
docker compose restart worker
```

---

## 📝 Tech Stack

- **API Framework:** FastAPI (async Python web framework)
- **Message Queue:** Apache Kafka (distributed event streaming)
- **Load Balancer:** Nginx (HTTP proxy and load balancer)
- **Container Orchestration:** Docker Compose
- **Load Testing:** Locust (Python-based load testing)
- **Monitoring UI:** Kafka UI (web interface for Kafka clusters)

---