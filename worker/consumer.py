import json
import time
import logging
import os
import signal
import sys
from kafka import KafkaConsumer
from kafka.errors import KafkaError
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WORKER] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "crm-events")

# Counters for the demo — in production use Prometheus
processed_count = 0
start_time = time.time()


def process_event(event: dict):
    """
    This is where your business logic lives.
    
    For this demo we simulate processing with a log statement.
    In a real CRM this might:
      - Update a user profile in the database
      - Trigger a churn prediction model
      - Send to a personalization engine
      - Update campaign analytics
    """
    global processed_count
    processed_count += 1

    event_type = event.get("event_type", "unknown")
    user_id = event.get("user_id", "unknown")
    event_id = event.get("event_id", "unknown")

    # Simulate different processing based on event type
    if event_type == "churn_signal":
        logger.info(f"⚠️  CHURN SIGNAL detected for user {user_id} — flagging for retention campaign")
    elif event_type == "purchase":
        logger.info(f"💰 Purchase event for user {user_id} — updating LTV model")
    elif event_type == "email_open":
        logger.info(f"📧 Email open for user {user_id} — updating engagement score")
    else:
        logger.info(f"✅ Processed [{event_type}] for user {user_id} | event_id={event_id}")

    # Print throughput every 10 events
    if processed_count % 10 == 0:
        elapsed = time.time() - start_time
        throughput = processed_count / elapsed
        logger.info(f"📊 Throughput: {throughput:.1f} events/sec | Total processed: {processed_count}")


def run_consumer():
    """
    Main consumer loop.
    
    consumer_group_id means multiple workers can share load — Kafka assigns
    different partitions to each worker in the same group. This is how you
    scale consumers horizontally without processing the same message twice.
    """
    logger.info(f"Starting consumer. Connecting to {KAFKA_BOOTSTRAP_SERVERS}, topic: {KAFKA_TOPIC}")

    # Retry loop — Kafka might not be ready immediately on startup
    consumer = None
    for attempt in range(10):
        try:
            consumer = KafkaConsumer(
                KAFKA_TOPIC,
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                group_id="crm-event-processors",   # consumer group for load sharing
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                auto_offset_reset="earliest",       # start from beginning if no offset stored
                enable_auto_commit=True,            # commit offsets automatically
                auto_commit_interval_ms=1000,       # commit every second
                session_timeout_ms=30000,
            )
            logger.info("✅ Connected to Kafka successfully")
            break
        except KafkaError as e:
            logger.warning(f"Attempt {attempt + 1}/10: Kafka not ready yet — {e}. Retrying in 3s...")
            time.sleep(3)

    if consumer is None:
        logger.error("❌ Could not connect to Kafka after 10 attempts. Exiting.")
        sys.exit(1)

    # Graceful shutdown on SIGTERM (what Docker sends on stop)
    def shutdown(signum, frame):
        logger.info("Shutting down consumer gracefully...")
        consumer.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)

    logger.info("👂 Listening for events...")

    for message in consumer:
        try:
            process_event(message.value)
        except Exception as e:
            logger.error(f"Error processing message: {e} | Raw: {message.value}")
            # In production: send to a Dead Letter Queue (DLQ) instead of just logging


if __name__ == "__main__":
    run_consumer()