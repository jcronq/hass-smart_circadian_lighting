from typing import Optional
import time
import asyncio
from custom_components.circadian_lighting import CONF_MAX_CT, CONF_MIN_CT
from custom_components.circadian_lighting.light_constants import CONF_MAX_BRIGHT, CONF_MIN_BRIGHT

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_TRANSITION,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
)


class Action:
    def __init__(
        self,
        entity_id: str,
        ct_key: str,
        ct_val: int,
        bright_val: Optional[int],
        transition: float,
        service: str,
    ):
        self.entity_id = entity_id
        self._ct_key = ct_key
        self._ct_val = ct_val
        self._bright_val = bright_val
        self._transition = transition
        self.service = service
        self._created_at = time.time()

    @property
    def created_at(self) -> float:
        return self._created_at

    @property
    def state(self) -> str:
        return "on"

    @property
    def service_data(self) -> dict:
        data = {
            ATTR_ENTITY_ID: self.entity_id,
            ATTR_TRANSITION: self._transition,
            self._ct_key: self._ct_val,
        }
        if self.bright_val is not None:
            data[ATTR_BRIGHTNESS] = self._bright_val
        return data

    def ct_basis_diff(self, hass_state):
        return abs(self._ct_val - hass_state[self._ct_key])

    def ct_basis_diff_normal(self, hass_state):
        return self.ct_basis_diff(hass_state) / (CONF_MAX_CT - CONF_MIN_CT)

    def bright_basis_diff(self, hass_state):
        return abs((self._bright_val - hass_state[ATTR_BRIGHTNESS]) / 255)

    def bright_basis_diff_normal(self, hass_state):
        return self.ct_basis_diff(hass_state) / (CONF_MAX_BRIGHT - CONF_MIN_BRIGHT)

    def states_match(self, hass_state):
        return hass_state == self.state

    def states_differ(self, hass_state):
        return not self.states_match(hass_state)


class ActionManager:
    def __init__(self, hass):
        self._hass = hass
        self._pending_futures = {}

    def call_action(self, domain: str, action: Action):
        call_future: asyncio.Future = self._hass.services.async_call(domain, action.service, action.service_data)
        call_future.add_done_callback(self.handle_completed_future)
        self._pending_futures[call_future] = action.entity_id

    def handle_completed_future(self, future: asyncio.Future):
        self._pending_futures.pop(future)

    def is_pending(self, entity_id: str):
        return entity_id in self._pending_futures
