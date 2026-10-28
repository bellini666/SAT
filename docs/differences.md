# Differences from the original SAT

SAT v2 is a fork of [Alexwijn/SAT](https://github.com/Alexwijn/SAT) by [Alex Wijnholds](https://github.com/Alexwijn), branched from upstream commit [`7d43560`](https://github.com/Alexwijn/SAT/commit/7d43560). The [commit history](https://github.com/bellini666/SAT/compare/7d43560...main) has every change.

The integration domain is still `sat`, so SAT v2 replaces the original in place. Existing config entries are migrated on first start.

## Safer control

- SAT hands the boiler back to its thermostat when it's turned off, unloaded, Home Assistant stops, or a required sensor is missing for 10 minutes. It takes over again once things are back.
- OTGW: SAT pauses while the gateway is offline and sets the boiler up again when it returns.
- After a restart, the PID resumes without a jump in the setpoint.
- A sensor counts as stale from its last report, so a steady value stays fresh. Non-numeric values count as missing, and SAT moves on to the next outside sensor.

## Steadier heating

- PWM has a 2 °C margin before switching off (without dynamic minimum setpoint), and the cycles-per-hour limit holds across resets.
- Room target changes and hot water runs don't disturb the PWM cycle, and closed valves keep PWM enabled.
- When the boiler keeps cutting burns short, PWM ends the on-phase early.
- PID fixes: the derivative fades instead of sticking, small changes inside the sample time aren't lost, and underfloor systems get underfloor gains.
- The heating curve stays flat once it's as warm outside as the target.

## Calibration

- A **Calibrate overshoot protection** button runs it at any time, in the background.
- It waits for a truly flat flow temperature, with configurable timeouts, and explains why it failed.
- The value can be edited in the options.
- Room thermostats are set to heat during calibration and restored afterwards. Hot water pauses it.

## Boiler monitoring

- New entities: boiler fault, boiler fault code, heating control problem.
- Short-cycle and overshoot warnings only count heating burns the boiler cut short. Hot water burns, including ones the boiler doesn't flag, are left out.
- Flame statistics survive restarts, and a gateway outage doesn't count as a burn ending.
- Sensors keep long-term statistics with correct units, and noisy attributes stay out of the recorder.

## Setup and options

- Advanced settings are a collapsed section of System Configuration, always available.
- New option: "Mode when nothing can be restored".
- The manufacturer is preselected, setup retries until MQTT is available, and diagnostics can be downloaded.
- Removing an entry deletes its stored data.

## Platform

- Requires Home Assistant 2026.10 or newer and `pyotgw>=2.2.3`.
- Modern tooling: dependencies managed with `uv`, linting and formatting with `ruff` (all rules), and type checking with `ty`.
- Fully typed code.
- 223 tests, up from 7, all run in CI along with hassfest and HACS validation.
