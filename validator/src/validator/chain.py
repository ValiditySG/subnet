"""Read registration through the chain sidecar before evaluating or weighing."""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from pylon_client.artanis import Config, Hotkey, IdentityName, PylonAuthToken, PylonClient
from pylon_client.artanis.v1 import Neuron


class ChainSettings(BaseSettings):
    """Private sidecar credentials are separate from Hippius credentials."""

    model_config = SettingsConfigDict(env_prefix="VALIDATOR_PYLON_", extra="ignore", hide_input_in_errors=True)
    service_address: str
    open_access_token: SecretStr
    identity_name: str
    identity_token: SecretStr

    def client(self) -> PylonClient:
        return PylonClient(
            Config(
                address=self.service_address,
                open_access_token=PylonAuthToken(self.open_access_token.get_secret_value()),
                identity_name=IdentityName(self.identity_name),
                identity_token=PylonAuthToken(self.identity_token.get_secret_value()),
            )
        )


def registered_neurons(netuid: int, hotkey: str) -> list[Neuron]:
    """Reject wrong-subnet sidecars and missing validator permits before assigning rewards.

    Raises:
        ValueError: If the configured subnet, registration or permit is invalid.
    """
    with ChainSettings.model_validate({}).client() as client:
        response = client.identity.get_recent_neurons()
        if int(client.identity.netuid) != netuid:
            raise ValueError("Chain sidecar identity belongs to another subnet")
        validator = response.neurons.get(Hotkey(hotkey))
        if validator is None or not validator.validator_permit:
            raise ValueError("Validator must be registered with a current validator permit")
        return list(response.neurons.values())
