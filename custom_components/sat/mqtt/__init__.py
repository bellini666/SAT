from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from collections import deque
from typing import TYPE_CHECKING, Any, override

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import CONF_MQTT_TOPIC
from ..coordinator import SatDataUpdateCoordinator
from ..helpers import snake_case

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from homeassistant.helpers.service_info.mqtt import ReceivePayloadType

_LOGGER: logging.Logger = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_SAVE_DELAY = 60


class SatMqttCoordinator(SatDataUpdateCoordinator):
    """Base class to manage fetching data using MQTT."""

    def __init__(
        self,
        hass: HomeAssistant,
        device_id: str,
        config_data: Mapping[str, Any],
        options: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(hass, config_data, options)

        self._device_id: str = device_id
        self._topic: str = config_data[CONF_MQTT_TOPIC]
        self._subscriptions: list[Callable[[], None]] = []
        self._messages: deque[dict[str, str]] = deque(maxlen=50)
        self._stale_keys: set[str] = set()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, snake_case(f"{self.__class__.__name__}_{device_id}")
        )

    @override
    @property
    def device_id(self) -> str:
        return self._device_id

    @override
    @property
    def messages(self) -> list[dict[str, str]]:
        return list(self._messages)

    @override
    async def async_setup(self) -> None:
        await self._load_stored_data()

        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            message = "MQTT is not available"
            raise ConfigEntryNotReady(message)

        for key in self.get_tracked_entities():
            self._subscriptions.append(
                await mqtt.async_subscribe(
                    self.hass,
                    self._get_topic_for_subscription(key),
                    self._create_message_handler(key),
                )
            )

    @override
    async def async_added_to_hass(self) -> None:
        await self.boot()

        await super().async_added_to_hass()

    @override
    async def async_will_remove_from_hass(self) -> None:
        while self._subscriptions:
            self._subscriptions.pop()()

        # Save the updated data to persistent storage
        await self._save_data()

        await super().async_will_remove_from_hass()

    async def _load_stored_data(self) -> None:
        """Load the data from persistent storage."""
        if (stored_data := await self._store.async_load()) is not None:
            restored = {key: value for key, value in stored_data.items() if value not in (None, "")}
            self._stale_keys = set(restored)
            self.async_set_updated_data(restored)

    async def _save_data(self) -> None:
        """Save the data to persistent storage."""
        await self._store.async_save(self.data)

    @abstractmethod
    def get_tracked_entities(self) -> list[str]:
        """Return the entities to track."""

    @abstractmethod
    def _get_topic_for_subscription(self, key: str) -> str:
        """Return the topic to subscribe to for the given key."""

    @abstractmethod
    def _get_topic_for_publishing(self) -> str:
        """Return the topic to publish commands to."""

    @abstractmethod
    async def boot(self) -> None:
        """Boot the device."""

    def _create_message_handler(self, key: str) -> Callable[[mqtt.ReceiveMessage], None]:
        """Create a message handler to process incoming MQTT messages."""

        @callback
        def message_handler(message: mqtt.ReceiveMessage) -> None:
            """Handle an incoming MQTT message and schedule an update."""
            self._messages.append(
                {
                    "received": dt_util.utcnow().isoformat(),
                    "topic": message.topic,
                    "payload": message.payload,
                }
            )

            try:
                # Process the payload and update the data property
                self._process_message_payload(key, message.payload)
            except Exception:
                _LOGGER.exception("Failed to process message for key '%s'", key)

            self._store.async_delay_save(lambda: dict(self.data), STORAGE_SAVE_DELAY)

        return message_handler

    def _live_value(self, key: str) -> str | None:
        """Return the value of a key unless it is stale.

        A value is stale when it was restored from storage or the device went offline since it
        arrived.
        """
        if key in self._stale_keys or not isinstance(value := self.data.get(key), str):
            return None

        return value

    def _process_message_payload(self, key: str, value: ReceivePayloadType) -> None:
        """Process and store the payload of a received MQTT message."""
        if key in self._stale_keys:
            self._stale_keys.discard(key)

            # Deleting marks the data dirty, so a stale value that comes back unchanged still
            # notifies the listeners
            del self.data[key]

        self.async_set_updated_data({key: value})

    async def _publish_command(self, payload: str, wait_time: float = 1.0) -> None:
        """Publish a command to the MQTT topic."""
        topic = self._get_topic_for_publishing()

        _LOGGER.debug(
            "Publishing MQTT command: payload='%s', topic='%s', simulation='%s'",
            payload,
            topic,
            self._simulation,
        )

        if self._simulation:
            return

        try:
            await mqtt.async_publish(hass=self.hass, topic=topic, payload=payload, qos=1)

            # Add a small delay to allow processing of the message
            await asyncio.sleep(wait_time)
        except Exception:
            _LOGGER.exception("Failed to publish MQTT command")
