# System Architecture — Deep Dive

## Full Request Flow

```
1. Client sends event
      ↓
2. Nginx receives (port 8081)
   • Checks rate limit (20 req/s per IP)
   • If allowed, picks backend via round-robin
      ↓
3. FastAPI instance receives
   • Checks app-level rate limit (100 req/min)
   • Enriches event (timestamp, event_id, IP)
   • Publishes to Kafka
   • Returns 202 Accepted immediately
      ↓
4. Kafka stores in partition
   • Event durably written to disk
   • Ack sent back to producer
      ↓
5. Worker polls Kafka
   • Consumer group ensures only 1 worker gets each event
   • Worker processes event
   • Commits offset (marks as done)
```

---

## Scaling Strategy

### Vertical vs Horizontal Scaling

**Vertical (scaling up):**
- Make each API server bigger (more CPU/RAM)
- Limited ceiling (~64 cores per machine)
- Single point of failure
- Expensive

**Horizontal (scaling out) — what we do:**
- Add more API servers behind load balancer
- No practical ceiling (can add 100s of instances)
- Redundancy built-in
- Cost-effective

### How to scale each layer:

| Layer | How to scale | Bottleneck indicator |
|-------|--------------|---------------------|
| API | Add replicas in docker-compose | CPU >80% |
| Kafka | Add brokers, increase partitions | Disk I/O, network |
| Workers | Add replicas | Consumer lag rising |
| Nginx | Rarely needed, handles 10K+ RPS easily | CPU >90% |

**Example: Scaling to 5 API instances**

In `docker-compose.yml`, change:
```yaml
api:
  deploy:
    replicas: 5  # was 3
```

Nginx automatically discovers all 5 via Docker DNS and load balances across them.

---

## Failure Scenarios & Handling

### Scenario 1: One API instance crashes

**What happens:**
- Nginx detects via health checks
- Routes traffic to remaining 2 instances
- Those instances handle load until crashed one restarts

**No data loss:** Events already in Kafka are safe

---

### Scenario 2: Kafka goes down

**What happens:**
- API tries to publish → fails
- API returns 503 to client
- Client retries (with exponential backoff)

**No silent data loss:** We fail loudly

---

### Scenario 3: Worker crashes mid-processing

**What happens:**
- Kafka offset wasn't committed yet
- Another worker picks up the same event
- Event gets processed (possibly twice)

**Implication:** Processing must be idempotent
- Example: "Set user.last_seen = timestamp" is idempotent
- Example: "Increment user.login_count" is NOT (use Kafka transactions in prod)

---

### Scenario 4: Traffic spike (10x normal)

**What happens:**
- Nginx rate limiter rejects excess (returns 429)
- Accepted requests queue in Kafka
- Workers process at steady rate
- Queue drains over time

**System stays healthy:** Kafka absorbs the burst

---

## Data Flow Guarantees

### At-least-once delivery

Our current setup provides **at-least-once**:
- Producer retries on failure → possible duplicate sends
- Consumer commits after processing → crash before commit = reprocess

For **exactly-once**:
- Use Kafka transactions (producer idempotence + transactional reads)
- Adds ~20% latency overhead
- Only needed for non-idempotent operations

---

## Monitoring Strategy

### Key Metrics to Track

**API layer:**
- Requests per second (by endpoint, status code)
- Response time (p50, p95, p99)
- Error rate (4xx, 5xx)
- Rate limit hits (how often are we rejecting?)

**Kafka layer:**
- Consumer lag (how far behind is the worker?)
- Throughput (messages/sec)
- Partition distribution (balanced?)

**Worker layer:**
- Processing time per event
- Failure rate
- Retry queue depth

### Alerting thresholds:

- 🔴 **Critical:** Consumer lag > 10,000 messages
- 🟡 **Warning:** API p95 latency > 100ms
- 🟢 **Info:** Rate limit rejections > 1% of traffic

---

## Cost Analysis (Hypothetical Production)

For 1M events/day:

| Component | Setup | Monthly Cost (AWS) |
|-----------|-------|-------------------|
| API (3x t3.medium) | ECS Fargate | ~$100 |
| Kafka (3x m5.large) | MSK | ~$450 |
| Workers (2x t3.small) | ECS Fargate | ~$50 |
| Load Balancer | ALB | ~$25 |
| **Total** | | **~$625/mo** |

Compare to serverless (Lambda + SQS): ~$120/mo but less control.

---

## Why This Architecture for CRM?

CRM platforms have unique constraints:

1. **Spiky traffic patterns**
   - Email campaign sends → surge in email_open events
   - Product launches → surge in page_view events
   - Our Kafka buffer smooths these spikes

2. **Multiple downstream consumers**
   - Churn prediction model needs events
   - Analytics dashboard needs events
   - Email trigger system needs events
   - Kafka lets each subscribe independently

3. **Regulatory compliance (GDPR, CCPA)**
   - Need event replay for right-to-be-forgotten audits
   - Kafka's retention lets you reprocess history

4. **Low latency matters for UX**
   - Personalization must be <100ms
   - Our API responds in <10ms, actual work happens async

---

## Comparison to Alternatives

### vs. Direct database writes

| Pattern | API Latency | Scalability | Coupling |
|---------|------------|-------------|----------|
| Direct DB | 50-200ms | Limited by DB | High |
| Kafka (ours) | <10ms | Near-infinite | Low |

### vs. RabbitMQ / Redis Pub/Sub

| Feature | Kafka | RabbitMQ | Redis |
|---------|-------|----------|-------|
| Durability | Disk | Disk | Memory |
| Replay | ✅ Yes | ❌ No | ❌ No |
| Throughput | 1M+/sec | 50K/sec | 100K/sec |
| Ops complexity | High | Medium | Low |

**Why we chose Kafka:** CRM needs replay capability and high throughput. Operational complexity is worth it at scale.

---

## Request Path Tracing Example

Let's trace a single `page_view` event through the entire system:

```
T+0ms:   User's browser sends POST /track to http://localhost:8081
         Body: {"user_id": "user_123", "event_type": "page_view", ...}

T+1ms:   Nginx receives request from IP 192.168.1.100
         - Checks rate limit zone: this IP has sent 15 req in last second
         - 15 < 20 limit → Allow
         - Round-robin: last request went to api-1, send this to api-2

T+2ms:   API instance #2 receives request
         - FastAPI validates JSON against CRMEvent model
         - Rate limiter checks: this IP has sent 80 req in last minute
         - 80 < 100 limit → Allow
         - Enriches event:
           * event_id: "abc-123-def"
           * server_timestamp: 1771234567.890
           * client_ip: "192.168.1.100"

T+3ms:   API calls get_producer() → returns singleton Kafka producer
         producer.send("crm-events", enriched_event)
         - Kafka client picks partition: hash(user_id) % num_partitions
         - user_123 hashes to partition 1

T+4ms:   Kafka broker receives message
         - Writes to disk (partition 1, offset 12345)
         - Sends ack back to producer

T+5ms:   API receives ack from Kafka
         - Returns HTTP 202 to Nginx
         Body: {"status": "accepted", "event_id": "abc-123-def"}

T+6ms:   Nginx forwards 202 response to client
         - Client's browser receives confirmation
         - Total round-trip: 6ms

T+100ms: Worker #2 polls Kafka
         - Consumer is assigned partition 1
         - Fetches batch of messages (including our event at offset 12345)
         - Calls process_event(event)
         - Logs: "✅ Processed [page_view] for user user_123"
         - Commits offset 12346 (next message to process)

T+200ms: Event processing complete
         - Hypothetically: updated user.last_seen in database
         - Hypothetically: triggered personalization engine
         - Hypothetically: logged to analytics warehouse
```

**Key insight:** The user got their 202 response at T+6ms, but the actual work didn't finish until T+200ms. The user never waited for that work.

---

## Nginx Configuration Explained

Let's break down what each part of `nginx.conf` does:

```nginx
upstream crm_api_cluster {
    server api:8000;
    keepalive 32;
}
```

- `upstream` defines a group of backend servers
- `api:8000` is Docker service name — Nginx resolves to all 3 IPs
- `keepalive 32` reuses TCP connections (huge perf win)

```nginx
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=20r/s;
```

- `$binary_remote_addr` = client IP in binary (compact)
- `zone=api_limit:10m` = 10MB shared memory for tracking IPs
- `rate=20r/s` = 20 requests per second per IP

```nginx
location /track {
    limit_req zone=api_limit burst=50 nodelay;
    proxy_pass http://crm_api_cluster;
}
```

- `burst=50` = allow 50 extra requests to queue (not reject immediately)
- `nodelay` = serve burst requests immediately (don't delay them)
- `proxy_pass` = forward to backend

---

## Kafka Consumer Group Deep Dive

**How consumer groups enable parallel processing:**

```
Topic: crm-events
Partitions: [0, 1]

Consumer Group: crm-event-processors
Members: [worker-1, worker-2]

Kafka's coordinator assigns:
- worker-1 → partition 0
- worker-2 → partition 1

Each partition is owned by exactly 1 worker.
Each worker can own multiple partitions.
```

**What happens when you add a 3rd worker?**

```
Before:
worker-1 → [partition 0]
worker-2 → [partition 1]

After (rebalance):
worker-1 → [partition 0]
worker-2 → [partition 1]  
worker-3 → []              (idle, waiting for more partitions)
```

To utilize worker-3, increase partitions:
```bash
docker exec kafka kafka-topics --alter \
  --topic crm-events \
  --partitions 3 \
  --bootstrap-server localhost:9092
```

Now:
```
worker-1 → [partition 0]
worker-2 → [partition 1]
worker-3 → [partition 2]  (now processing!)
```

---

## Event Schema Evolution

As the CRM grows, event schemas change. Here's how you'd handle it in production:

**Version 1 (current):**
```json
{
  "user_id": "user_123",
  "event_type": "page_view",
  "properties": {"page": "/pricing"}
}
```

**Version 2 (add device tracking):**
```json
{
  "user_id": "user_123",
  "event_type": "page_view",
  "properties": {"page": "/pricing"},
  "device": {
    "type": "mobile",
    "os": "iOS"
  }
}
```

**Problem:** Old workers will fail parsing new events!

**Solution:** Schema Registry (Confluent)
- Store schemas with version numbers
- Producers tag events with schema ID
- Consumers check schema, handle any version
- Backward/forward compatibility rules enforced

---

## Performance Tuning Knobs

If you needed to 10x this system's throughput:

### API Layer
- Increase worker processes in uvicorn (currently 2)
- Tune connection pool sizes
- Enable gzip compression in Nginx

### Kafka Layer
- Add more brokers (currently 1)
- Increase partitions (currently 2 → 20)
- Tune `batch.size` and `linger.ms` for producer

### Worker Layer
- Increase consumer `max_poll_records` (fetch more per batch)
- Add more worker replicas
- Parallelize processing within each worker (thread pool)

### Nginx Layer
- Increase `worker_connections` (currently 1024)
- Tune `proxy_buffering` settings
- Enable HTTP/2

---

## Security Considerations (Not Implemented)

In production, you'd need:

1. **API Authentication**
   - API keys or OAuth2 tokens
   - Rate limiting per customer, not per IP

2. **Network Security**
   - TLS termination at Nginx
   - mTLS between services
   - Network policies (only Nginx can reach API)

3. **Kafka Security**
   - SASL authentication
   - ACLs per topic
   - Encryption at rest and in transit

4. **Data Privacy**
   - PII encryption before Kafka
   - GDPR compliance (right to be forgotten)
   - Audit logs for data access

---

## Next Steps if Continuing This Project

### Phase 3: Advanced Observability (1 day)
- Add Prometheus exporters to API and workers
- Deploy Grafana with pre-built dashboards
- Set up alerting rules (PagerDuty/Slack)

### Phase 4: Dead Letter Queue (2 hours)
- Create `crm-events-dlq` topic
- Catch exceptions in worker, send to DLQ
- Build retry mechanism with exponential backoff

### Phase 5: Schema Registry (3 hours)
- Deploy Confluent Schema Registry
- Define Avro schemas for events
- Enforce compatibility rules

### Phase 6: Stream Processing (1 day)
- Add Kafka Streams or Flink
- Real-time aggregations (events per user per hour)
- Windowed computations (rolling averages)

### Phase 7: Production Deployment (2 days)
- Terraform for AWS infrastructure
- GitHub Actions CI/CD pipeline
- Multi-environment setup (dev/staging/prod)

---

## Comparison to Real-World CRM Architectures

**HubSpot:**
- Similar Kafka-based ingestion
- ~10 billion events/day
- 100+ consumer applications

**Segment:**
- Same pattern: API → Queue → Workers
- Uses both Kafka and SQS
- Multi-cloud (AWS + GCP)

**Salesforce:**
- Event Bus built on Kafka
- Platform Events API = our /track endpoint
- CometD for real-time streaming to clients

**What we built is the core pattern they all use.** The difference is scale (billions vs thousands of events) and surrounding infrastructure (ML pipelines, data warehouses, etc).

---

## Interview Questions You Can Now Answer

**"How would you handle a traffic spike 100x normal?"**

> "Kafka acts as a buffer. The API would accept all requests and queue them in Kafka. Workers would process at a steady rate. The queue would grow temporarily but drain over time. If the spike is sustained, I'd add more workers via `docker-compose scale worker=10`. The consumer group automatically rebalances partitions across all workers."

**"What if a worker keeps crashing on a bad event?"**

> "Right now it would reprocess forever. In production I'd add a retry counter to each message. After 3 failures, send it to a dead letter queue for manual inspection. This prevents one bad message from blocking the entire queue."

**"How do you ensure no events are lost?"**

> "Multiple layers: (1) Kafka persists to disk with replication, (2) producer waits for ack before returning success, (3) API returns 503 if Kafka is down so client can retry, (4) consumer commits offsets only after successful processing. The weakest link is currently the API — if it crashes between Kafka ack and returning to client, that one event could be lost. For zero data loss, I'd add a write-ahead log."

**"How does this scale to multiple data centers?"**

> "Kafka supports multi-datacenter replication. You'd run a Kafka cluster in each region, with one region designated as primary. Producers write to the local cluster. MirrorMaker replicates across regions. If the primary region fails, promote a replica region to primary. Consumers in each region read from their local cluster for low latency."

---

This architecture handles 99% of event-driven use cases. The remaining 1% (global consistency, sub-millisecond latency) requires different tradeoffs.
