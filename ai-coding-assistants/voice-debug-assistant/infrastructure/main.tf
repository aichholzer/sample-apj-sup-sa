terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  default = "us-east-1"
}

# --- IAM Role for Lambda ---

resource "aws_iam_role" "lambda" {
  name = "vox-demo-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "lambda_xray" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

resource "aws_iam_role_policy" "lambda_dynamodb" {
  name = "dynamodb-access"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan"]
      Resource = aws_dynamodb_table.users.arn
    }]
  })
}

# --- DynamoDB Table ---

resource "aws_dynamodb_table" "users" {
  name         = "vox-demo-users"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"

  attribute {
    name = "user_id"
    type = "S"
  }
}

# Seed some test data
resource "aws_dynamodb_table_item" "user1" {
  table_name = aws_dynamodb_table.users.name
  hash_key   = aws_dynamodb_table.users.hash_key
  item = jsonencode({
    user_id = { S = "u-001" }
    name    = { S = "Alice" }
    email   = { S = "alice@example.com" }
  })
}

resource "aws_dynamodb_table_item" "user2" {
  table_name = aws_dynamodb_table.users.name
  hash_key   = aws_dynamodb_table.users.hash_key
  item = jsonencode({
    user_id = { S = "u-002" }
    name    = { S = "Bob" }
    email   = { S = "bob@example.com" }
  })
}

# --- Lambda: get_users (has a deliberate bug) ---

data "archive_file" "get_users" {
  type        = "zip"
  source_file = "${path.module}/lambda/get_users.py"
  output_path = "${path.module}/.build/get_users.zip"
}

resource "aws_lambda_function" "get_users" {
  function_name    = "vox-demo-get-users"
  role             = aws_iam_role.lambda.arn
  handler          = "get_users.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.get_users.output_path
  source_code_hash = data.archive_file.get_users.output_base64sha256
  timeout          = 10

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.users.name
    }
  }
}

resource "aws_cloudwatch_log_group" "get_users" {
  name              = "/aws/lambda/${aws_lambda_function.get_users.function_name}"
  retention_in_days = 7
}

# --- Lambda: create_user (working correctly) ---

data "archive_file" "create_user" {
  type        = "zip"
  source_file = "${path.module}/lambda/create_user.py"
  output_path = "${path.module}/.build/create_user.zip"
}

resource "aws_lambda_function" "create_user" {
  function_name    = "vox-demo-create-user"
  role             = aws_iam_role.lambda.arn
  handler          = "create_user.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.create_user.output_path
  source_code_hash = data.archive_file.create_user.output_base64sha256
  timeout          = 10

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.users.name
    }
  }
}

resource "aws_cloudwatch_log_group" "create_user" {
  name              = "/aws/lambda/${aws_lambda_function.create_user.function_name}"
  retention_in_days = 7
}

# --- API Gateway ---

resource "aws_apigatewayv2_api" "api" {
  name          = "vox-demo-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gw.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      method         = "$context.httpMethod"
      path           = "$context.path"
      status         = "$context.status"
      error          = "$context.error.message"
      integrationErr = "$context.integrationErrorMessage"
      latency        = "$context.responseLatency"
    })
  }
}

resource "aws_cloudwatch_log_group" "api_gw" {
  name              = "/aws/apigateway/vox-demo"
  retention_in_days = 7
}

# GET /users -> get_users
resource "aws_apigatewayv2_integration" "get_users" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.get_users.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "get_users" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "GET /users"
  target    = "integrations/${aws_apigatewayv2_integration.get_users.id}"
}

resource "aws_lambda_permission" "get_users" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_users.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

# POST /users -> create_user
resource "aws_apigatewayv2_integration" "create_user" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.create_user.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "create_user" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "POST /users"
  target    = "integrations/${aws_apigatewayv2_integration.create_user.id}"
}

resource "aws_lambda_permission" "create_user" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.create_user.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

# --- Outputs ---

output "api_url" {
  value = aws_apigatewayv2_api.api.api_endpoint
}

output "get_users_log_group" {
  value = aws_cloudwatch_log_group.get_users.name
}
