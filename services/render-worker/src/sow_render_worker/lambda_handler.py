import json
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def handler(event, context):
    logger.info("Received event: %s", json.dumps(event))
    return {"statusCode": 200, "body": json.dumps({"message": "Render worker invoked"})}
