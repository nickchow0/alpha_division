from shared.redis_client import get_redis

MONITORED_SERVICES = ["data", "analysis", "execution", "alerts"]


def get_service_statuses() -> list:
    """
    Return a list of dicts, one per monitored service, with:
      - name: service name
      - alive: True if heartbeat key exists with positive TTL
      - ttl: seconds remaining on the heartbeat key (-2 = missing, -1 = no TTL)
    """
    r = get_redis()
    result = []
    for service in MONITORED_SERVICES:
        ttl = r.ttl(f"heartbeat:{service}")
        result.append({
            "name": service,
            "alive": ttl > 0,
            "ttl": ttl,
        })
    return result
