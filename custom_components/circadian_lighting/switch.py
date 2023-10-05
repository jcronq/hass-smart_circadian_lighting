"""
Circadian Lighting Switch for Home-Assistant.
"""
from typing import List, Optional, Dict

import time
import logging
from itertools import repeat
import asyncio
import queue
from custom_components.circadian_lighting import CIRCADIAN_LIGHTING_UPDATE_TOPIC


from homeassistant.components.light import (
    ATTR_COLOR_TEMP,
    ATTR_RGB_COLOR,
    ATTR_XY_COLOR,
)
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.light import is_on
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import (
    SERVICE_TURN_ON,
    STATE_ON,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_state_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify
from homeassistant.util.color import (
    color_RGB_to_xy,
    color_temperature_to_rgb,
)

from .action import Action, ActionManager

_LOGGER = logging.getLogger(__name__)

ICON = "mdi:theme-light-dark"


def _difference_between_states(from_state, to_state):
    start = "Lights adjusting because "
    if from_state is None and to_state is None:
        return start + "both states None"
    if from_state is None:
        return start + f"from_state: None, to_state: {to_state}"
    if to_state is None:
        return start + f"from_state: {from_state}, to_state: None"

    changed_attrs = ", ".join(
        [f"{key}: {val}" for key, val in to_state.attributes.items() if from_state.attributes.get(key) != val]
    )
    if from_state.state == to_state.state:
        return start + (
            f"{from_state.entity_id} is still {to_state.state} but" f" these attributes changes: {changed_attrs}."
        )
    elif changed_attrs != "":
        return start + (
            f"{from_state.entity_id} changed from {from_state.state} to"
            f" {to_state.state} and these attributes changes: {changed_attrs}."
        )
    else:
        return start + (
            f"{from_state.entity_id} changed from {from_state.state} to" f" {to_state.state} and no attributes changed."
        )


class HassState:
    def __init__(self, hass_state):
        self._hass_state = hass_state

    @property
    def ct(self) -> int:
        pass

    @property
    def bri(self) -> int:
        pass

    @property
    def on(self) -> bool:
        return self._hass_state.state == "on"


def geometric_mean(arg_1: float, arg_2: float) -> float:
    return (arg_1 * arg_2) ** 0.5


# Note to self:  I just fininshed configuring this class.
# next step is to integrate it into whatever home assistant needs
class LightStateManager:
    def __init__(self, hass):
        self._hass = hass
        self.actions_per_second = 5
        self._seconds_per_action = 1 / self.actions_per_second
        self._action_list: List[Action] = []
        self._pending_futures = {}
        self._run = True
        self._action_manager = ActionManager(hass)
        self._event_loop = asyncio.get_running_loop()
        self._loop_future = asyncio.run_coroutine_threadsafe(self.run_action_loop(), self._event_loop)
        self._loop_future.add_done_callback(self.loop_complete)

    async def lazy_action(self, action: Action):
        self._action_list.append(action)

    def filter_actions(self, actions: List[Action]) -> List[Action]:
        # Filter out all actions with the same entity_id except for the most recently created one.
        most_recent_actions: Dict[str, Action] = {}
        for action in reversed(actions):
            # If we haven't seen this entity_id before and it is the most recent we've seen, add it to the dict
            if (
                action.entity_id not in most_recent_actions
                and action.created_at > most_recent_actions[action.entity_id].created_at
            ):
                most_recent_actions[action.entity_id] = action
        return list(most_recent_actions.values())

    def prioritize_actions(self, actions: List[Action]) -> List[Action]:
        """
        Sort the actions in the following order:
            1. Actions that change the state from on to off or vice versa
            2. The degree to which there is a change to the color temperature or brightness
        """

        def priority(action: Action) -> float:
            if self._action_manager.is_turning_on(action):
                return 0.0
            elif self._action_manager.is_turning_off(action):
                return 0.5
            else:
                # Max val is 3.0 here.  So if ct and bri are maximally off target (1.0 each), then the priority is 1.0
                # this falls just behind turning off in priority, but ahead of every other action.
                return 3.0 - (
                    action.ct_basis_diff_normal(self._hass.states(action.entity_id))
                    + action.bright_basis_diff_normal(self._hass.states(action.entity_id))
                )

        return sorted(actions, key=priority)

    def next_action(self) -> Optional[Action]:
        if len(self._action_list) <= 0:
            return None
        self._acton_list = self.filter_actions(self._action_list)
        self._acton_list = self.prioritize_actions(self._action_list)

        for idx, next_action in enumerate(self._action_list):
            if self._action_manager.is_pending(next_action.entity_id):
                continue
            self._action_list.pop(idx)
            return next_action
        return None

    async def run_action_loop(self):
        while self._run:
            start = time.time()
            action: Optional[Action] = self.next_action()
            if action is not None and not self.execute_action(action):
                # Don't sleep if the action was not executed
                continue
            await asyncio.sleep(self._seconds_per_action - (time.time() - start))

    async def execute_action(self, light_action: Action) -> bool:
        """Returns true if the action was executed."""
        hass_state = self._hass.states(light_action.entity_id)
        if (
            light_action.states_differ(hass_state)
            or 5 <= light_action.ct_basis_diff(hass_state)
            or 5 <= light_action.bright_basis_diff(hass_state)
        ):
            self._action_manager.call_action(LIGHT_DOMAIN, light_action)
            return True
        return False

    def start_loop(self):
        # IF we're still supposed to be running, then we exited the loop prematurely.
        # .. Reschedule the loop
        if self._run:
            self._event_loop = asyncio.get_running_loop()
            self._loop_future = asyncio.run_coroutine_threadsafe(self.run_action_loop(), self._event_loop)
            self._loop_future.add_done_callback(self.loop_complete)


class CircadianSwitch(SwitchEntity, RestoreEntity):
    """Representation of a Circadian Lighting switch."""

    def __init__(
        self,
        hass,
        circadian_lighting,
        name,
        ct_source,
        bright_source,
        groups,
        initial_transition,
        transition,
        only_once,
    ):
        """Initialize the Circadian Lighting switch."""
        self.hass = hass
        self._circadian_lighting = circadian_lighting
        self._name = name
        self._entity_id = f"switch.circadian_lighting_{slugify(name)}"
        self._state = None
        self._icon = ICON
        self._hs_color = None
        self._groups = groups
        self._ct_source = ct_source
        self._bright_source = bright_source
        self._initial_transition = initial_transition
        self._transition = transition
        self._only_once = only_once
        self._lights = {light: group for group in self._groups for light in group.lights}
        self._light_state_manager = LightStateManager(self.hass)

    @property
    def entity_id(self):
        """Return the entity ID of the switch."""
        return self._entity_id

    @property
    def name(self):
        """Return the name of the device if any."""
        return self._name

    @property
    def is_on(self):
        """Return true if circadian lighting is on."""
        return self._state

    @property
    def light_entities(self) -> List[str]:
        """Returns a list of all lights under management"""
        return list(self._lights.keys())

    async def async_added_to_hass(self):
        """Call when entity about to be added to hass."""
        # Add callback
        self.async_on_remove(async_dispatcher_connect(self.hass, CIRCADIAN_LIGHTING_UPDATE_TOPIC, self._update_switch))

        # Add listeners
        async_track_state_change(self.hass, self.light_entities, self._light_state_changed, to_state="on")

        if self._state is not None:  # If not None, we got an initial value
            return

        state = await self.async_get_last_state()
        self._state = state and state.state == STATE_ON

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        return self._icon

    @property
    def hs_color(self):
        return self._hs_color

    @property
    def extra_state_attributes(self):
        """Return the attributes of the switch."""
        return {
            "hs_color": self._hs_color,
            "brightness": self.auto_brightness,
            "colortemp": self._color_temperature(),
        }

    async def async_turn_on(self, **kwargs):
        """Turn on circadian lighting."""
        self._state = True
        await self._force_update_switch()

    async def async_turn_off(self, **kwargs):
        """Turn off circadian lighting."""
        self._state = False
        self._hs_color = None

    def auto_ct(self):
        return self.hass.states.get(self._ct_source).state

    def auto_rgb(self):
        r, g, b = color_temperature_to_rgb(self.auto_ct())
        return int(r), int(g), int(b)

    def auto_xy(self):
        return color_RGB_to_xy(*self.auto_rgb())

    @property
    def auto_brightness(self) -> float:
        if self._disable_brightness_adjust:
            return None
        delta_brightness = self._max_brightness - self._min_brightness
        bri_state = self.hass.states.get(self._bright_source).state
        return (delta_brightness * bri_state) + self._min_brightness

    @property
    def auto_bright(self) -> int:
        return int(self.auto_brightness / 100) * 254

    async def _update_switch(self, lights=None, transition=None, force=False):
        if self._only_once and not force:
            return
        self._hs_color = self._calc_hs()
        await self._adjust_lights(lights or self._lights, transition)

    async def _force_update_switch(self, lights=None):
        return await self._update_switch(lights, transition=self._initial_transition, force=True)

    def _should_adjust(self):
        return self._state

    async def _adjust_lights(self, group, transition):
        if not self._should_adjust():
            return

        if transition is None:
            transition = self._transition

        tasks = []
        for group in group.groups:
            # Replace with a check against the states being equal
            for light in group.light:
                if not is_on(self.hass, light):
                    continue

                if group.light_type == "ct":
                    ct_key = ATTR_COLOR_TEMP
                    ct_val = int(self.auto_ct())
                elif group.light_type == "rgb":
                    ct_key = ATTR_RGB_COLOR
                    ct_val = int(self.auto_rgb())
                elif group.light_type == "xy":
                    ct_key = ATTR_XY_COLOR
                    ct_val = int(self.auto_xy())

                action = Action(
                    entity_id=light,
                    ct_key=ct_key,
                    ct_val=ct_val,
                    bri_val=self.auto_bright if group.bright_control_enabled else None,
                    transition=transition,
                    service=SERVICE_TURN_ON,
                )

                _LOGGER.debug(
                    "Scheduling 'light.turn_on' with the following 'service_data': %s",
                    action.service_data,
                )
                self._light_state_manager.lazy_action(action)

    async def _light_state_changed(self, entity_id, from_state, to_state):
        if from_state is None or from_state.state != "on":
            _LOGGER.debug(_difference_between_states(from_state, to_state))
            await self._force_update_switch(lights=[entity_id])

    async def _state_changed(self, entity_id, from_state, to_state):
        _LOGGER.debug(_difference_between_states(from_state, to_state))
        await self._force_update_switch()
