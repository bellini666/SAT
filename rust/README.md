# Thermostat control module

SAT ships `custom_components/sat/control.wasm` inside the HACS integration. Home Assistant installs the pure-Python `pywasm` dependency listed in the manifest. Installing SAT requires no Rust compiler or separate service.

Each room has its own PID instance. The Python adapter supplies monotonic timestamps and the same decimal rounding used for stored state. The shared module computes the heating curve, autotuning adjustments, PWM decisions, calibration results and boiler setpoints. Home Assistant owns entity state, gateway commands, flame history and timers.

To rebuild from the repository root, run

```sh
rustup toolchain install 1.95.0 --profile minimal --target wasm32-unknown-unknown
sh rust/build.sh
uv run --locked pytest
```

Commit the rebuilt module with its sources. CI checks that they match before running the integration tests.
