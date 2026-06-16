import json
import os
import uuid

import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])


def handler(event, context):
    """Create a new user."""
    try:
        body = json.loads(event.get("body", "{}"))
        user_id = f"u-{uuid.uuid4().hex[:6]}"

        table.put_item(Item={
            "user_id": user_id,
            "name": body.get("name", "Unknown"),
            "email": body.get("email", ""),
        })

        return {
            "statusCode": 201,
            "body": json.dumps({"user_id": user_id, "message": "User created"}),
        }
    except Exception as e:
        print(f"ERROR: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
