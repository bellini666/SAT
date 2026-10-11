# AGENTS.md

This file provides guidance to coding agents (Claude Code, Codex, etc.) when working with code in this repository.

SAT (Smart Autotune Thermostat) is a Home Assistant custom integration in `custom_components/sat`. It drives a boiler setpoint from a heating curve plus PID, over OpenTherm (OTGW serial/MQTT, ESPHome), EMS (MQTT), or a plain on/off switch.

## Commands

Package manager is `uv` (Python >= 3.14.2). Pre-commit hooks run through `prek`.

```sh
uv sync                                   # install deps
uv run pytest -q --tb=line                # all tests
uv run pytest tests/test_pwm.py::test_name  # single test
uv run ruff check --fix && uv run ruff format
uv run ty check                           # type check, all rules are errors
prek run --files <changed files>
docker compose up                         # dev HA on :8123 with custom_components mounted
```

Ruff runs with `select = ["ALL"]` (line length 100); see `pyproject.toml` for the ignore list. CI runs ruff check, ruff format `--check`, `ty check`, pytest, hassfest and HACS validation.

## Architecture

- **Setup** (`__init__.py`): `async_setup_entry` resolves a coordinator via `SatDataUpdateCoordinatorFactory`, awaits `coordinator.async_setup()`, builds `SatClimate`, and stores both in `entry.runtime_data` (`SatRuntimeData`). Other platforms (sensor, number, binary_sensor, button) read from `runtime_data`. Unload and HA stop call `async_release_control`, which cancels calibration, stops the loop and releases the boiler.
- **Config entry migrations**: `SatFlowHandler.VERSION` in `config_flow.py` must match the last version-gated block in `async_migrate_entry` (`__init__.py`). Bumping the schema means adding a new `< N` block there and a case in `tests/test_migration.py`.
- **Coordinators** (`coordinator.py`): `SatDataUpdateCoordinator` is the backend contract. `coordinator_factory.py` maps the configured mode to a subclass in `esphome/`, `mqtt/` (OpenTherm and EMS), `serial/` (pyotgw), `switch/`, `simulator/`, or `fake/`. Backends implement the abstract `device_id`, `device_type`, `setpoint`, `device_active`, `member_id`, override the `async_set_*` control methods they support, and advertise capabilities through the `supports_*` properties. The climate entity checks `supports_*` rather than the backend type.
- **Control loop** (`climate.py`, `SatClimate.async_control_heating_loop`): requested setpoint = `heating_curve.value + pid.output`, low-pass filtered, then gated by PWM (`pwm.py`). PWM on uses the minimum setpoint (`minimum_setpoint.py`), PWM off a fixed low value, and the result is clipped to the coordinator's maximum setpoint. Multi-room control lives in `area.py` (per-area PIDs; the main PID uses the max error across areas). Flame/cycle tracking is in `flame.py`, overshoot protection calibration in `overshoot_protection.py`. Sensor, thermostat, area and boiler-temperature changes call `schedule_control_heating_loop`, debounced unless `force=True`, plus a periodic tick. Missing inputs hold the last setpoint for a grace period, then release the boiler.
- **Manufacturers** (`manufacturer.py`, `manufacturers/`): one name-only class per brand, loaded by name from `CONF_MANUFACTURER`. Brand-specific behaviour lives in checks against the manufacturer type in the coordinator and climate code, not in the subclasses.

## Tests

- `tests/conftest.py` provides an `entry` fixture driven by four parametrized fixtures: `domains` (list of `(domain, count)` to set up first, usually template sensors), `config` (their YAML), `data` and `options` (merged over `DEFAULT_USER_DATA` from `tests/const.py`, which uses the `fake` mode). `climate` and `coordinator` fixtures pull from `entry.runtime_data`; `coordinator` is a `SatFakeCoordinator`.
- Tests parametrize `"domains, data, options, config"` together; see `tests/test_climate.py` for the pattern. Prefer driving behaviour through the fake coordinator and real template sensors over mocking SAT internals.
- `asyncio_mode = "auto"`, so async tests need no marker.
