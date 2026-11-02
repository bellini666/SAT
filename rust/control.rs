//! SAT's synchronous control decisions. Each room PID has its own module instance.
mod pid;

#[link(wasm_import_module = "sat")]
extern "C" {
    fn decimal_round(value: f64, places: i32) -> f64;
}

fn rounded(value: f64, places: i32) -> f64 {
    // Home Assistant exposes CPython's decimal rounding in persisted controller state.
    unsafe { decimal_round(value, places) }
}

#[no_mangle]
pub extern "C" fn heating_curve(target: f64, outside: f64) -> f64 {
    let offset = outside.min(target) - 20.0;
    4.0 * (target - 20.0) + 0.03 * (offset * offset) - 0.4 * offset
}

#[no_mangle]
pub extern "C" fn curve_setpoint(base: f64, coefficient: f64, curve: f64) -> f64 {
    rounded(base + coefficient / 4.0 * curve, 1)
}

#[no_mangle]
pub extern "C" fn curve_coefficient(base: f64, setpoint: f64, curve: f64) -> f64 {
    rounded(4.0 * (setpoint - base) / curve, 1)
}

#[no_mangle]
pub extern "C" fn autotune_sample(coefficient: f64, derivative: f64) -> f64 {
    let step: f64 = match derivative.abs() {
        0.0 => 0.0,
        d if d < 0.5 => 0.1,
        d if d > 1.0 => 0.3,
        _ => 0.2,
    };
    coefficient - step.copysign(derivative)
}

#[no_mangle]
pub extern "C" fn requested_setpoint(curve: f64, output: f64, minimum: f64) -> f64 {
    rounded((curve + output).max(minimum), 1)
}

#[no_mangle]
pub extern "C" fn filtered_setpoint(previous: f64, requested: f64, alpha: f64) -> f64 {
    rounded(alpha * requested + (1.0 - alpha) * previous, 1)
}

// PWM mode: 0 = idle/bypassed, 1 = on, 2 = off.
#[no_mangle]
pub extern "C" fn control_setpoint(
    requested: f64,
    mode: i32,
    valves_open: i32,
    pwm_minimum: f64,
    maximum: f64,
    cold: f64,
    minimum: f64,
) -> f64 {
    let target = match mode {
        0 if valves_open != 0 && requested >= cold => requested,
        1 => pwm_minimum,
        _ => minimum,
    };
    target.min(maximum)
}

#[no_mangle]
pub extern "C" fn minimum_setpoint(
    stable: i32,
    modulation: f64,
    flow: f64,
    return_temp: f64,
    base_return: f64,
    configured: f64,
    factor: f64,
) -> f64 {
    let candidate = if stable != 0 {
        (1.0 - modulation / 100.0) * flow - 3.0
    } else {
        configured + (return_temp - base_return) * factor
    };
    candidate.max(30.0)
}

#[no_mangle]
pub extern "C" fn duty_fraction(requested: f64, boiler: f64, base: f64) -> f64 {
    ((requested - base) / (boiler.max(base + 1.0) - base)).clamp(0.0, 1.0)
}

// Export the two durations together through linear memory.
use std::cell::Cell;
thread_local! { static DURATIONS: Cell<[f64; 2]> = const { Cell::new([0.0; 2]) }; }

#[no_mangle]
pub extern "C" fn duty_cycle(
    demand: f64,
    automatic: i32,
    space_heating_flame: i32,
    max_time: f64,
    max_count: f64,
) -> *const [f64; 2] {
    let period = 3600.0 / max_count.max(1.0);
    let min_on = 180.0;
    let lower = min_on / period;
    let (on, off) = if automatic == 0 {
        (demand * max_time, (1.0 - demand) * max_time)
    } else if demand < lower / 2.0 {
        let on = if space_heating_flame != 0 {
            min_on
        } else {
            0.0
        };
        (on, 2.0 * period - on)
    } else if demand <= lower {
        (min_on, min_on / demand - min_on)
    } else if demand <= 1.0 - lower {
        (period * demand, period * (1.0 - demand))
    } else if demand <= 1.0 - lower / 2.0 {
        (min_on / (1.0 - demand) - min_on, min_on)
    } else {
        (2.0 * period, 0.0)
    };
    DURATIONS.with(|times| {
        times.set([on, off]);
        times.as_ptr()
    })
}

// 0 = hold, 1 = start, 2 = stop, 3 = hourly limit, 4 = cut-short stop.
#[no_mangle]
pub extern "C" fn pwm_transition(
    mode: i32,
    elapsed: f64,
    on: f64,
    off: f64,
    startup: f64,
    capped: i32,
    short_burns: i32,
) -> i32 {
    if mode == 1 && short_burns != 0 {
        return 4;
    }
    if mode != 1 && on >= startup && (elapsed >= off || mode == 0) {
        return if capped != 0 { 3 } else { 1 };
    }
    if mode != 2 && (on < startup || (elapsed >= on && off > 0.0) || mode == 0) {
        return 2;
    }
    0
}

#[no_mangle]
pub extern "C" fn pwm_enabled(
    forced: i32,
    protection: i32,
    calculated: f64,
    dynamic: i32,
    version: i32,
    minimum: f64,
    hysteresis: f64,
    latched: i32,
) -> i32 {
    let enabled = if forced != 0 {
        true
    } else if protection == 0 || calculated.is_nan() {
        false
    } else if dynamic == 0 {
        minimum + hysteresis > calculated
    } else if version == 1 {
        minimum > calculated
    } else {
        latched != 0
    };
    enabled as i32
}

#[no_mangle]
pub extern "C" fn pwm_setpoint(
    current: f64,
    flow: f64,
    active: i32,
    inactive: i32,
    adjusting_allowed: i32,
    latest_on: f64,
    average_on: f64,
) -> f64 {
    if active != 0 && adjusting_allowed != 0 && latest_on > 6.0 {
        let target = flow - 3.0;
        if current.is_nan() {
            return target;
        }
        return target.clamp(current - 1.0, current + 1.0);
    }
    if inactive != 0 && (average_on.is_nan() || average_on < 60.0) {
        return flow + 10.0;
    }
    current
}

#[no_mangle]
pub extern "C" fn startup_flow(
    current: f64,
    previous: f64,
    on: i32,
    hot_water: i32,
    elapsed: f64,
    startup: f64,
    alpha: f64,
) -> f64 {
    if on == 0 || hot_water != 0 {
        return previous;
    }
    if elapsed <= startup {
        alpha * current + (1.0 - alpha) * previous
    } else {
        current
    }
}

// The adapter records sample timestamps and supplies the window statistics.
#[no_mangle]
pub extern "C" fn calibration_plateau(
    flow_span: f64,
    sensor_step: f64,
    tolerance: f64,
    slope: f64,
    plateau: f64,
    modulation: f64,
    floor: f64,
    modulation_span: f64,
    setpoint: f64,
    margin: f64,
    modulation_tolerance: f64,
    stability: f64,
) -> i32 {
    if flow_span > tolerance.max(sensor_step + 0.1) || slope.abs() > sensor_step {
        return 0;
    }
    if modulation <= floor + modulation_tolerance {
        return if plateau >= setpoint - margin { 1 } else { 2 };
    }
    if modulation_span > stability {
        return 0;
    }
    3
}

#[no_mangle]
pub extern "C" fn calibration_formula(setpoint: f64, modulation: f64) -> f64 {
    rounded((100.0 - modulation) / 100.0 * setpoint, 1)
}

// Priority is domestic hot water, cold boiler, then PWM suppression.
#[no_mangle]
pub extern "C" fn modulation_state(hot_water: i32, setpoint: f64, minimum: f64, pwm: i32) -> i32 {
    if hot_water != 0 {
        return 0;
    }
    if setpoint.is_nan() || setpoint <= minimum {
        return 1;
    }
    if pwm != 0 {
        return 2;
    }
    3
}
