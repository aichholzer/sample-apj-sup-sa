import json
import os

import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])


def handler(event, context):
    """Get all users. BUG: uses 'userId' instead of 'user_id'."""
    try:
        response = table.scan()
        items = response.get("Items", [])

        # BUG: attribute is 'user_id' but code references 'userId'
        users = []
        for item in items:
            users.append({
                "id": item["userId"],  # <-- THIS IS THE BUG: should be 'user_id'
                "name": item["name"],
                "email": item["email"],
            })

        return {
            "statusCode": 200,
            "body": json.dumps({"users": users}),
        }
    except Exception as e:
        print(f"ERROR: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
