"""
Production Secret Management -- HashiCorp Vault Compatible
=========================================================
Implements a layered secret resolution strategy:
  1. HashiCorp Vault (production -- if VAULT_ADDR set)
  2. Cloud provider secrets (AWS/GCP -- if cloud env detected)
  3. Environment variables (staging/local)
  4. .env file (development only -- loaded by agents/config.py's
     load_dotenv() before this module's env-var fallback ever runs)

This replaces direct os.getenv() calls for sensitive credentials.
Dr Agent recommendation: "Replace .env file with HashiCorp Vault
or cloud provider native secret management."

Usage:
    from backend.secrets import secrets
    api_key = secrets.get("LYZR_API_KEY")
"""

import logging
import os

logger = logging.getLogger(__name__)


class SecretManager:
    """
    Layered secret resolution with Vault-first strategy.
    Falls back gracefully through each provider.
    """

    def __init__(self):
        self._vault_client = None
        self._vault_available = False
        self._secret_cache: dict[str, str] = {}
        self._provider = "environment"
        self._initialize()

    def _initialize(self):
        """Initialize the highest-available secret provider."""

        # Layer 1: HashiCorp Vault
        vault_addr = os.getenv("VAULT_ADDR")
        vault_token = os.getenv("VAULT_TOKEN")
        if vault_addr and vault_token:
            try:
                import hvac
                client = hvac.Client(url=vault_addr, token=vault_token)
                if client.is_authenticated():
                    self._vault_client = client
                    self._vault_available = True
                    self._provider = "hashicorp_vault"
                    logger.info(
                        f"Secret Manager: HashiCorp Vault connected at {vault_addr}"
                    )
                    return
            except Exception as e:
                logger.warning(f"Vault connection failed: {e} -- falling back")

        # Layer 2: AWS Secrets Manager
        if os.getenv("AWS_SECRET_ARN"):
            try:
                import boto3
                self._aws_client = boto3.client("secretsmanager")
                self._provider = "aws_secrets_manager"
                logger.info("Secret Manager: AWS Secrets Manager active")
                return
            except Exception as e:
                logger.warning(f"AWS Secrets Manager failed: {e} -- falling back")

        # Layer 3: GCP Secret Manager
        if os.getenv("GOOGLE_CLOUD_PROJECT"):
            try:
                from google.cloud import secretmanager
                self._gcp_client = secretmanager.SecretManagerServiceClient()
                self._provider = "gcp_secret_manager"
                logger.info("Secret Manager: GCP Secret Manager active")
                return
            except Exception as e:
                logger.warning(f"GCP Secret Manager failed: {e} -- falling back")

        # Layer 4: Environment variables (default -- includes values loaded
        # from .env by agents/config.py's load_dotenv() call, since that
        # populates os.environ before any secrets.get() call is made)
        self._provider = "environment_variables"
        logger.info(
            "Secret Manager: Using environment variables "
            "(set VAULT_ADDR+VAULT_TOKEN for production Vault)"
        )

    def get(self, key: str, default: str = "") -> str:
        """
        Resolve a secret from the highest-available provider.
        Results are cached in-memory to minimize secret store calls.
        """
        if key in self._secret_cache:
            return self._secret_cache[key]

        value = self._resolve(key, default)

        if value:
            self._secret_cache[key] = value

        return value

    def _resolve(self, key: str, default: str) -> str:
        """Resolve from the active provider."""

        if self._provider == "hashicorp_vault" and self._vault_client:
            try:
                vault_path = os.getenv("VAULT_SECRET_PATH", "secret/data/sre-agent")
                secret = self._vault_client.secrets.kv.v2.read_secret_version(
                    path=vault_path.replace("secret/data/", ""),
                    mount_point="secret",
                )
                data = secret["data"]["data"]
                if key in data:
                    logger.debug(f"Secret '{key}' resolved from Vault")
                    return data[key]
            except Exception as e:
                logger.warning(f"Vault read failed for '{key}': {e}")

        if self._provider == "aws_secrets_manager" and hasattr(self, "_aws_client"):
            try:
                arn = os.getenv("AWS_SECRET_ARN")
                response = self._aws_client.get_secret_value(SecretId=arn)
                import json
                data = json.loads(response["SecretString"])
                if key in data:
                    return data[key]
            except Exception as e:
                logger.warning(f"AWS Secrets read failed for '{key}': {e}")

        # Always fallback to environment
        value = os.getenv(key, default)
        if value:
            logger.debug(f"Secret '{key}' resolved from environment")
        else:
            logger.debug(f"Secret '{key}' not found in any provider")
        return value

    @property
    def provider(self) -> str:
        return self._provider

    def health(self) -> dict:
        """Returns secret manager health for /system/info endpoint."""
        return {
            "provider": self._provider,
            "vault_available": self._vault_available,
            "cached_keys": len(self._secret_cache),
            "vault_addr": os.getenv("VAULT_ADDR", "not configured"),
        }


# Singleton
secrets = SecretManager()
