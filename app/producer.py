import json
import logging
from kafka import KafkaProducer
from kafka.errors import KafkaError
import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# This is a module-level singleton — one producer shared across all requests
_producer = None


def get_producer() -> KafkaProducer:
    """
    Returns a shared KafkaProducer instance.
    Creates it on first call (lazy initialization).
    This avoids the overhead of creating a new connection per request.
    """
    global _producer
    if _producer is None:
        _producer = KafkaProducer(
            bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            # How many broker acknowledgments required before considering send successful
            # acks=1 means leader broker acknowledged — good balance of speed vs safety
            acks=1,
            # If Kafka is temporarily unavailable, retry up to 3 times
            retries=3,
        )
        logger.info("Kafka producer initialized")
    return _producer


def publish_event(topic: str, event: dict) -> bool:
    """
    Publishes a single event dict to the given Kafka topic.
    Returns True on success, False on failure.
    
    The .send() is async — it returns a Future.
    .get(timeout=5) blocks until broker confirms receipt.
    For even higher throughput, you'd remove .get() and let it truly fire-and-forget.
    """
    try:
        producer = get_producer()
        future = producer.send(topic, value=event)
        future.get(timeout=5)  # wait for broker ack
        return True
    except KafkaError as e:
        logger.error(f"Failed to publish event: {e}")
        return False