"""Pytest configuration and shared fixtures."""

import os

import boto3
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from moto import mock_aws

# Ensure test environment uses mock AWS
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "ca-central-1"
os.environ["ENVIRONMENT"] = "test"
os.environ["COGNITO_USER_POOL_ID"] = "ca-central-1_test123"
os.environ["COGNITO_CLIENT_ID"] = "test-client-id"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-minimum-32-chars-long"
os.environ["JOB_TABLE_NAME"] = "idp-provision-jobs-test"


@pytest.fixture(scope="session")
def aws_credentials():
    """Mock AWS credentials for moto."""
    return {
        "aws_access_key_id": "testing",
        "aws_secret_access_key": "testing",
        "aws_session_token": "testing",
        "region_name": "ca-central-1",
    }


@pytest.fixture
def mock_ddb_table(aws_credentials):
    """Create a mock DynamoDB table for testing."""
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="ca-central-1")
        ddb.create_table(
            TableName="idp-provision-jobs-test",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield ddb


@pytest.fixture
def valid_provision_payload():
    return {
        "service_name": "test-payments-api",
        "environment": "dev",
        "template_id": "eks-microservice",
        "size": "sm",
        "owner_team": "payments-team",
        "cost_centre": "CC-1234",
        "region": "ca-central-1",
    }


@pytest.fixture
def auth_headers():
    """
    Return headers with a mock Bearer token.
    In tests, the auth middleware is bypassed via dependency override.
    """
    return {"Authorization": "Bearer mock-test-token"}
