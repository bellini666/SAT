# Setup

Add SAT under **Settings → Devices & services → Add integration** and search for "Smart Autotune Thermostat". If SAT finds an OpenTherm Gateway or EMS-ESP on your network or MQTT broker, it also shows up there as a discovered device.

The setup asks for:

1. [Connection](#connection)
2. [Sensors](#sensors)
3. [Heating system](#heating-system)
4. [Areas](#areas)
5. [Calibration](#calibration)
6. [Manufacturer](#manufacturer)

**Reconfigure** on the integration page runs these steps again, from Sensors on. Everything else lives in [Options](options.md).

## Connection

Pick how SAT talks to your boiler:

| Choice | For | You enter |
|---|---|---|
| MQTT Gateway | OTGW or EMS-ESP over MQTT | The mode (OpenTherm or EMS-ESP), a name and the MQTT topic. For OTGW, also the device ID (`otgw-XXXXXXXXXXXX`). |
| Serial Gateway | OTGW over serial or the network | A name and the gateway URL |
| ESPHome | An ESPHome OpenTherm device | A name and the device |
| PID Thermostat | A `switch`, `valve` or `input_boolean` | A name, the entity and the water temperature your heating runs at (default 50 °C) |
| Simulated Gateway | Development only | |

For OTGW, the MQTT topic is the *MQTT Top Topic* from the OTGW firmware settings. For EMS-ESP, it's the EMS-ESP base topic.

### ESPHome

Pick the main ESPHome device, not one of its sub-devices. SAT finds the OpenTherm entities by name, so your ESPHome YAML must use exactly these names:

<details>
<summary>Minimal ESPHome YAML</summary>

```yaml
# Insert usual esphome configuration (board, api, ota, etc.)

opentherm:
  in_pin: # insert in pin
  out_pin: # insert out pin
  ch_enable: true
  dhw_enable: true

number:
  - platform: opentherm
    t_dhw_set:
      name: t_dhw_set
      step: 1
      restore_value: true
      initial_value: 45
    t_set:
      name: t_set
      restore_value: true
      initial_value: 10
    max_t_set:
      name: max_t_set
      step: 1
      restore_value: true
    max_rel_mod_level:
      name: max_rel_mod_level
      min_value: 0
      max_value: 100
      step: 1
      initial_value: 100
      restore_value: true

sensor:
  - platform: opentherm
    rel_mod_level:
      name: rel_mod_level
    device_id:
      name: device_id
    t_boiler:
      name: t_boiler
    t_ret:
      name: t_ret
    max_capacity:
      name: max_capacity
    min_mod_level:
      name: min_mod_level
    t_dhw_set_lb:
      name: t_dhw_set_lb
    t_dhw_set_ub:
      name: t_dhw_set_ub

binary_sensor:
  - platform: opentherm
    flame_on:
      name: flame_on
    dhw_active:
      name: dhw_active

switch:
  - platform: opentherm
    dhw_enable:
      name: dhw_enable
    ch_enable:
      name: ch_enable
```

The [ESPHome OpenTherm docs](https://esphome.io/components/opentherm.html) list the other entities.

</details>

## Sensors

- **Inside sensor**: the temperature of the room SAT controls. A sensor that reports two decimals and updates every 30 seconds works best.
- **Outside sensor**: a temperature sensor or a weather entity. You can pick several, and SAT uses the first one that has a reading.
- **Humidity sensor** (optional): only used by [Thermal Comfort](options.md#advanced).

## Heating system

Radiators, underfloor heating or a heat pump. This sets the [heating curve](how-it-works.md#heating-curve) and the starting PID settings, so pick the one you have. The on/off thermostat skips this step.

## Areas

Skip this step if SAT is your only thermostat.

- **Thermostat**: your wall thermostat. When you change its target, SAT follows.
- **Radiators**: radiator thermostats (TRVs) in the same room as the inside sensor.
- **Rooms**: thermostats in other rooms, each with its own target. SAT heats when any of them asks for heat.

With rooms added, [Heating Mode](options.md#general) decides whether SAT also heats for the other rooms or only for the main one.

## Calibration

Only for OpenTherm and EMS-ESP. Pick one:

- **Calibrate and determine your overshoot protection value** (recommended): SAT runs the boiler at its lowest power and measures the water temperature it settles at. This takes up to about 50 minutes. See [Calibration](how-it-works.md#calibration).
- **Manually enter the overshoot protection value**: if you already know it. [This discussion](https://github.com/Alexwijn/SAT/discussions/36) explains how to find it by hand.
- **Manually enter PID values**: not recommended. Only for people who know PID tuning.

You can calibrate again any time with the **Calibrate overshoot protection** button.

The on/off thermostat asks about **Automatic Gains** instead. Keep them on, or turn them off and enter the PID values in [Options](options.md#general).

## Manufacturer

When SAT asks for your boiler manufacturer, it preselects the one it detects. Change it if it's wrong, since some brands need special handling.

---

Next: [Options](options.md) · [How SAT works](how-it-works.md)
