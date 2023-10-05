from custom_components.circadian_lighting.light_constants import (
    CONF_BRIGHT_SOURCE,
    CONF_CT_SOURCE,
    CONF_DISABLE_BRIGHTNESS_ADJUST,
    DEFAULT_INITIAL_TRANSITION,
    DEFAULT_TRANSITION,
    CONF_MIN_BRIGHT,
    CONF_MAX_BRIGHT,
    CONF_GROUPS,
    CONF_LIGHT_ENTITIES,
    CONF_LIGHTS_COLOR_CONTROL,
    CONF_INITIAL_TRANSITION,
    CONF_TRANSITION,
    CONF_ONLY_ONCE,
    DEFAULT_MAX_BRIGHT,
    DEFAULT_MIN_BRIGHT,
)
from homeassistant.const import (
    CONF_NAME,
    CONF_PLATFORM,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.components.light import VALID_TRANSITION
import voluptuous as vol

from .switch import CircadianSwitch, ControlGroup, DOMAIN

# TODO: Rework the config to a list of dictionaries of structures fitting
#         [
#           - Group Name
#           - Lights
#         ]
PLATFORM_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PLATFORM): "circadian_lighting",
        vol.Optional(CONF_NAME, default="Circadian Lighting"): cv.string,
        vol.Required(CONF_CT_SOURCE): cv.entity_id,
        vol.Required(CONF_BRIGHT_SOURCE): cv.entity_id,
        vol.Optional(CONF_INITIAL_TRANSITION, default=DEFAULT_INITIAL_TRANSITION): VALID_TRANSITION,
        vol.Optional(CONF_TRANSITION, default=DEFAULT_TRANSITION): VALID_TRANSITION,
        vol.Required(CONF_GROUPS): [
            {
                vol.Required(CONF_LIGHT_ENTITIES): cv.entity_ids,
                vol.Optional(CONF_LIGHTS_COLOR_CONTROL, default="ct"): vol.Any("ct", "rgb", "xy"),
                vol.Optional(CONF_DISABLE_BRIGHTNESS_ADJUST, default=False): cv.boolean,
                vol.Optional(CONF_MIN_BRIGHT, default=DEFAULT_MIN_BRIGHT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=100)
                ),
                vol.Optional(CONF_MAX_BRIGHT, default=DEFAULT_MAX_BRIGHT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=100)
                ),
                vol.Optional(CONF_ONLY_ONCE, default=False): cv.boolean,
            }
        ],
    }
)


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Circadian Lighting switches."""
    circadian_lighting = hass.data.get(DOMAIN)
    if circadian_lighting is not None:
        switch = CircadianSwitch(
            hass,
            circadian_lighting,
            name=config.get(CONF_NAME),
            disable_brightness_adjust=config.get(CONF_DISABLE_BRIGHTNESS_ADJUST),
            ct_source=config.get(CONF_CT_SOURCE),
            bright_source=config.get(CONF_BRIGHT_SOURCE),
            groups=[ControlGroup(group) for group in config.get(CONF_GROUPS)],
            min_brightness=config.get(CONF_MIN_BRIGHT),
            max_brightness=config.get(CONF_MAX_BRIGHT),
            initial_transition=config.get(CONF_INITIAL_TRANSITION),
            transition=config.get(CONF_TRANSITION),
            only_once=config.get(CONF_ONLY_ONCE),
        )
        add_devices([switch])

        return True
    else:
        return False
