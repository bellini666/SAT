# Differences from the original SAT

SAT v2 is a fork of [Alexwijn/SAT](https://github.com/Alexwijn/SAT) by [Alex Wijnholds](https://github.com/Alexwijn), branched from upstream commit [`7d43560`](https://github.com/Alexwijn/SAT/commit/7d43560). The [commit history](https://github.com/bellini666/SAT/compare/7d43560...main) has every change.

The integration domain is still `sat`, so SAT v2 replaces the original in place. Existing config entries are migrated on first start.

## Control loop

- Control starts once Home Assistant has finished starting.
- When a required sensor goes missing, SAT holds the last setpoint for 10 minutes, then hands the boiler back to its thermostat. It takes over again once the sensor returns.
- At startup, control goes back to the thermostat when the boiler is not heating.
- Turning SAT off, unloading the integration and stopping Home Assistant all hand the boiler back to its thermostat.
- The setpoint SAT shows is capped at the maximum setpoint, same as the one the boiler gets.
- The heating curve stays flat once it's as warm outside as the target.
- Heat pumps use the radiator curve.
- Mode, target and preset are restored after a restart. Leaving a preset returns to the last custom target.

## PID

- After a restart, the PID picks up where it was without a jump in the setpoint.
- A held derivative fades out over time, and it decays inside the deadband.
- The integral only grows on the current error.
- Error changes that arrive inside the sample time are applied once it passes.
- The PID resets when the inside sensor goes stale.
- Underfloor systems get underfloor gains.
- Room PIDs wait until their heating curve is known.
- Gains chosen during setup or reconfigure are stored in the options.
- Heating curve autotune steps in the right direction.

## PWM and minimum setpoint

- PWM switches off only once the setpoint is 2 °C above the minimum, so a setpoint hovering around it doesn't use up cycles.
- The cycles-per-hour limit holds across resets. Once it's reached, PWM stays off until the hour ends.
- Changing a room's target or mode keeps the current PWM cycle. Changing SAT's own target, preset or mode starts a new one.
- When the boiler cuts a burn short twice in one on-phase, PWM ends the on-phase. The cycle keeps its length.
- Hot water runs don't affect the PWM duty cycle.
- A PWM cycle with no off time stays on.
- With every valve closed, SAT doesn't heat: the setpoint drops to 10 °C and PWM waits.
- The cycles-per-hour choices depend on the heating system: 3 or 4 for radiators, 2 or 3 for heat pumps.
- Dynamic minimum setpoint v1 starts from the minimum in use, and v2 turns on PWM when the boiler overshoots.
- The minimum setpoint is kept when boiler values are missing, and deleted with the config entry.

## Overshoot protection calibration

- A **Calibrate overshoot protection** button runs a calibration at any time. Calibration runs in the background, one at a time.
- The overshoot protection value can be edited in the options and can't exceed the maximum setpoint.
- The flame and settle timeouts are configurable (10 and 40 minutes by default).
- The value is taken from a flat flow temperature (at most 0.02 °C/min), and a flow that flickers by one degree still counts.
- A failed run shows the reason and, on a timeout, the highest flow temperature reached. A flow that settles below the setpoint is reported as `below_setpoint`.
- Radiator and room thermostats are set to heat during calibration and restored afterwards.
- Hot water demand pauses calibration and releases the boiler.
- Geminox boilers calibrate at MM=10.
- Turning SAT off, unloading and stopping Home Assistant cancel a running calibration first.
- Reconfigure keeps the value in use and can't run while a calibration runs.

## Flame and cycle statistics

- Hot water burns stay out of the cycle statistics and the short-cycle warnings. A burn counts as hot water when:
  - the boiler reports hot water during it,
  - it starts within 30 s of a hot water request, or
  - its flow rises more than 10 °C above its setpoint, as with a Remeha comfort preheat.
- Short-cycle and ignition-overshoot warnings only count burns the boiler cut short. Burns while SAT is off, paused or offline, and burns older than four hours, don't count.
- Average on time covers whole cycles only.
- A burn only ends on a live flame-off. A gateway outage doesn't end it, and health shows "insufficient data" until the first flame reading after a restart.
- "Stuck off" is timed from when the flow drops below the setpoint, so a boiler waiting to relight isn't flagged. PWM off phases pause the timer.
- "Stuck on" is timed from the end of heat demand.
- Flame statistics survive reloads and restarts.
- Boiler health waits two minutes after setup before judging.

## Entities and diagnostics

- New entities: a boiler fault binary sensor, a boiler fault code sensor, a heating control problem sensor and a calibration button.
- Power, consumption, heating curve and error sensors keep long-term statistics. Consumption is a rate in m³/h, and the error is a temperature difference.
- Fast-changing climate attributes (boiler temperature derivative, flame timing, PID terms, duty cycle) stay out of the recorder, which keeps the database small.
- Config entry diagnostics are available, with device IDs redacted.
- Entity names come from translations under the SAT device.
- SAT actions (`reset_integral`, `pulse_width_modulation`) are entity services.
- A sensor stays fresh as long as it reports, even when its value doesn't change.
- A non-numeric sensor or MQTT value counts as missing. SAT then tries the next outside sensor.
- The Summer Simmer attributes use the raw inside temperature.

## Hot water

- The hot water setpoint can be set while the gateway is online. It's remembered once the boiler accepts it, and a restored value is shown.

## Backends

- OTGW: when the gateway goes offline, SAT pauses. When it comes back, SAT sets up the boiler again (SH, SW, MM and TC).
- OTGW: restored boiler values count as stale until the gateway sends fresh ones.
- OTGW: MM is re-sent unless the boiler already reports it. TC is cleared on release.
- OTGW: old firmware (up to v0.10.2) is supported for the minimum modulation level.
- OTGW: requires `pyotgw>=2.2.3`.
- MQTT: gateway data is saved at most once a minute and deleted with the config entry.
- ESPHome: the device must be a main device, and its entities are released on unload.
- EMS-ESP: the manufacturer step stays empty when none is detected.
- Switch: valves are supported, and the default temperature setting is 50 °C.
- Simulator: each entry gets its own ID, and unloading doesn't reset the gateway.

## Rooms and areas

- A room that's turned off is left out ([Alexwijn/SAT#170](https://github.com/Alexwijn/SAT/issues/170)).
- HVAC mode changes go to every climate that supports them.
- Room target changes are recorded unless presets are synced. With synced presets, only Home changes are recorded.
- Climates without a temperature are tolerated.
- The window sensor is on when any window is open, off when all are closed, and unknown when none can be read. A window that closes before the minimum open time doesn't change the preset.

## Config flow and options

- The options have General, Presets and System Configuration menus, with advanced settings in a collapsed section.
- New option: "Mode when nothing can be restored" (heat or off).
- The manufacturer step preselects the detected manufacturer, or the saved one on reconfigure.
- DHCP discovery leaves MQTT entries alone.
- Setup retries until MQTT is available. A repair issue appears after 5 minutes of failed setup.
- Config entry migrations carry old settings through.

## Platform and tooling

- Requires Home Assistant 2026.10 or newer.
- No Sentry error reporting.
- Per-loop logs are at debug level.
- Dependencies managed with uv. Linting with ruff, type checking with ty, all enforced in CI with pytest, hassfest and HACS validation.
- A much larger test suite.
