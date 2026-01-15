"""Redis Streams helper functions for publishing and consuming messages."""

import logging
from typing import Any, Dict, List, Optional

import redis

from backend.redis import get_redis_client

logger = logging.getLogger(__name__)


def publish_to_stream(stream_key: str, data: Dict[str, Any]) -> str:
    """
    Publish data to a Redis stream.
    
    Args:
        stream_key: The Redis stream key
        data: Dictionary of field-value pairs to publish
        
    Returns:
        Message ID of the published message
        
    Raises:
        redis.RedisError: If publishing fails
    """
    client = get_redis_client()
    try:
        message_id = client.xadd(stream_key, data)
        logger.debug(f"Published message {message_id} to stream {stream_key}")
        return message_id
    except Exception as e:
        logger.error(f"Failed to publish to stream {stream_key}: {e}")
        raise


def consume_stream(
    stream_key: str,
    consumer_group: str,
    consumer_name: str,
    count: int = 1,
    block: Optional[int] = None,
    start_id: str = ">",
) -> List[Dict[str, Any]]:
    """
    Consume messages from a Redis stream using a consumer group.
    
    Args:
        stream_key: The Redis stream key
        consumer_group: Name of the consumer group
        consumer_name: Name of this consumer instance
        count: Maximum number of messages to return (default: 1)
        block: Block for up to this many milliseconds if no messages available (None = no blocking)
        start_id: Stream ID to start reading from (">" = new messages only)
        
    Returns:
        List of messages, where each message is a dict with 'id' and 'data' keys
        
    Raises:
        redis.RedisError: If consumption fails
    """
    client = get_redis_client()
    
    try:
        # Ensure consumer group exists
        try:
            client.xgroup_create(stream_key, consumer_group, id="0", mkstream=True)
            logger.debug(f"Created consumer group {consumer_group} for stream {stream_key}")
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
            # Group already exists, which is fine
        
        # Read messages
        streams = {stream_key: start_id}
        if block is not None:
            messages = client.xreadgroup(
                consumer_group,
                consumer_name,
                streams,
                count=count,
                block=block,
            )
        else:
            messages = client.xreadgroup(
                consumer_group,
                consumer_name,
                streams,
                count=count,
            )
        
        # Parse messages into a more usable format
        result = []
        if messages:
            for stream, stream_messages in messages:
                for msg_id, msg_data in stream_messages:
                    result.append({
                        "id": msg_id,
                        "data": msg_data,
                    })
        
        if result:
            logger.debug(
                f"Consumed {len(result)} message(s) from stream {stream_key} "
                f"(group: {consumer_group}, consumer: {consumer_name})"
            )
        
        return result
        
    except Exception as e:
        logger.error(
            f"Failed to consume from stream {stream_key} "
            f"(group: {consumer_group}, consumer: {consumer_name}): {e}"
        )
        raise
