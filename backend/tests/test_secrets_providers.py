"""
backend/tests/test_secrets_providers.py

Tests for backend/secrets.py's Vault/AWS/GCP provider layers -- previously
only the environment-variable fallback layer was exercised (see
backend/tests/test_secrets.py). hvac is a real installed dependency
(backend/requirements.txt), so its Client is mocked directly; boto3 is not
installed (it's an optional, bring-your-own dependency for the AWS layer),
so it's simulated via sys.modules injection rather than actually installed
-- a real, working Vault/AWS deployment would import exactly these same
names for real.
"""
import sys
from unittest.mock import MagicMock, patch

from backend.secrets import SecretManager


def test_vault_layer_active_and_resolves_secret(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://vault.example.com")
    monkeypatch.setenv("VAULT_TOKEN", "test-token")
    monkeypatch.setenv("VAULT_SECRET_PATH", "secret/data/sre-agent")

    mock_client = MagicMock()
    mock_client.is_authenticated.return_value = True
    mock_client.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": {"MY_KEY": "vault-value"}}}

    with patch("hvac.Client", return_value=mock_client):
        sm = SecretManager()
        assert sm.provider == "hashicorp_vault"
        assert sm.get("MY_KEY") == "vault-value"
        assert sm.health()["vault_available"] is True


def test_vault_connection_exception_falls_back(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://vault.example.com")
    monkeypatch.setenv("VAULT_TOKEN", "bad-token")
    monkeypatch.delenv("AWS_SECRET_ARN", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    with patch("hvac.Client", side_effect=Exception("connection refused")):
        sm = SecretManager()
        assert sm.provider == "environment_variables"
        assert sm.health()["vault_available"] is False


def test_vault_not_authenticated_falls_back(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://vault.example.com")
    monkeypatch.setenv("VAULT_TOKEN", "bad-token")
    monkeypatch.delenv("AWS_SECRET_ARN", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    mock_client = MagicMock()
    mock_client.is_authenticated.return_value = False
    with patch("hvac.Client", return_value=mock_client):
        sm = SecretManager()
        assert sm.provider == "environment_variables"


def test_vault_read_failure_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://vault.example.com")
    monkeypatch.setenv("VAULT_TOKEN", "test-token")
    monkeypatch.setenv("SOME_KEY_XYZ", "env-value")

    mock_client = MagicMock()
    mock_client.is_authenticated.return_value = True
    mock_client.secrets.kv.v2.read_secret_version.side_effect = Exception("network error")
    with patch("hvac.Client", return_value=mock_client):
        sm = SecretManager()
        assert sm.provider == "hashicorp_vault"
        assert sm.get("SOME_KEY_XYZ") == "env-value"


def test_vault_key_missing_from_data_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://vault.example.com")
    monkeypatch.setenv("VAULT_TOKEN", "test-token")
    monkeypatch.setenv("OTHER_KEY_XYZ", "env-fallback")

    mock_client = MagicMock()
    mock_client.is_authenticated.return_value = True
    mock_client.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": {}}}
    with patch("hvac.Client", return_value=mock_client):
        sm = SecretManager()
        assert sm.get("OTHER_KEY_XYZ") == "env-fallback"


def test_aws_secrets_manager_active_and_resolves(monkeypatch):
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    monkeypatch.setenv("AWS_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123456789:secret:sre-agent")

    mock_boto3 = MagicMock()
    mock_aws_client = MagicMock()
    mock_aws_client.get_secret_value.return_value = {"SecretString": '{"MY_KEY": "aws-value"}'}
    mock_boto3.client.return_value = mock_aws_client

    with patch.dict(sys.modules, {"boto3": mock_boto3}):
        sm = SecretManager()
        assert sm.provider == "aws_secrets_manager"
        assert sm.get("MY_KEY") == "aws-value"


def test_aws_client_creation_failure_falls_back(monkeypatch):
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.setenv("AWS_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123456789:secret:sre-agent")

    mock_boto3 = MagicMock()
    mock_boto3.client.side_effect = Exception("no credentials configured")
    with patch.dict(sys.modules, {"boto3": mock_boto3}):
        sm = SecretManager()
        assert sm.provider == "environment_variables"


def test_aws_read_failure_falls_back_to_env(monkeypatch):
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.setenv("AWS_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123456789:secret:sre-agent")
    monkeypatch.setenv("FALLBACK_KEY_XYZ", "env-value")

    mock_boto3 = MagicMock()
    mock_aws_client = MagicMock()
    mock_aws_client.get_secret_value.side_effect = Exception("access denied")
    mock_boto3.client.return_value = mock_aws_client

    with patch.dict(sys.modules, {"boto3": mock_boto3}):
        sm = SecretManager()
        assert sm.provider == "aws_secrets_manager"
        assert sm.get("FALLBACK_KEY_XYZ") == "env-value"


def test_gcp_env_set_but_library_missing_falls_back(monkeypatch):
    # google-cloud-secretmanager isn't installed in this environment (it's
    # an optional, bring-your-own dependency, same as boto3) -- this
    # exercises the real ImportError -> fallback path honestly, rather
    # than mocking a library that isn't actually present.
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.delenv("AWS_SECRET_ARN", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-test-project")
    sm = SecretManager()
    assert sm.provider == "environment_variables"


def test_layer_priority_vault_beats_aws_and_gcp(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://vault.example.com")
    monkeypatch.setenv("VAULT_TOKEN", "test-token")
    monkeypatch.setenv("AWS_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123456789:secret:sre-agent")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-test-project")

    mock_client = MagicMock()
    mock_client.is_authenticated.return_value = True
    with patch("hvac.Client", return_value=mock_client):
        sm = SecretManager()
        assert sm.provider == "hashicorp_vault"
