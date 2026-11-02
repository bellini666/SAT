use super::rounded;
use std::cell::Cell;

#[derive(Clone, Copy, Default)]
struct Pid {
    kp: f64,
    ki: f64,
    kd: f64,
    version: i32,
    automatic: bool,
    underfloor: bool,
    gain: f64,
    coefficient: f64,
    weight: f64,
    deadband: f64,
    interval: f64,
    sample: f64,
    error: f64,
    previous: f64,
    curve: f64,
    integral: f64,
    slope: f64,
    updated: f64,
    integrated: f64,
    differentiated: Option<f64>,
}

impl Pid {
    fn gains(&self) -> [f64; 3] {
        if !self.automatic {
            return [self.kp, self.ki, self.kd];
        }
        if self.version == 3 {
            let divisor = if self.underfloor { 4.0 } else { 3.0 };
            let kp = rounded(self.coefficient * self.curve / divisor, 6);
            return [kp, rounded(kp / 8400.0, 6), rounded(0.07 * 8400.0 * kp, 6)];
        }
        let factor = if self.underfloor { 0.243 } else { 0.33 };
        let aggression = if self.underfloor {
            73.0
        } else if self.version == 1 {
            99.0
        } else {
            81.5
        };
        let ki = if self.version == 1 {
            self.curve / 73900.0
        } else {
            self.gain * (self.curve / 7200.0)
        };
        [
            rounded(self.gain * factor * self.curve, 6),
            rounded(ki, 6),
            rounded(self.gain * aggression * self.weight * self.curve, 6),
        ]
    }

    fn reset(&mut self, now: f64, error: f64, curve: f64) {
        self.error = error;
        self.previous = error;
        self.curve = curve;
        self.slope = 0.0;
        self.integral = 0.0;
        self.updated = now;
        self.integrated = now;
        self.differentiated = Some(now);
    }

    fn integrate(&mut self, now: f64, error: f64, curve: f64, force: bool) {
        if self.error.abs() > self.deadband && error.abs() <= self.deadband {
            self.integrated = now;
        }
        if error.abs() > self.deadband {
            self.integral = 0.0;
            return;
        }
        let elapsed = now - self.integrated;
        if force || elapsed >= self.interval {
            self.integral = (self.integral + self.gains()[1] * error * elapsed)
                .min(curve)
                .max(-curve);
            self.integrated = now;
        }
    }

    fn differentiate(&mut self, now: f64, error: f64, alpha1: f64, alpha2: f64) {
        let Some(last) = self.differentiated else {
            self.differentiated = Some(now);
            return;
        };
        if now <= last {
            return;
        }
        let slope = if self.error.abs() > self.deadband {
            (error - self.error) / (now - last)
        } else {
            0.0
        };
        let filtered = alpha1 * slope + (1.0 - alpha1) * self.slope;
        self.slope = alpha2 * filtered + (1.0 - alpha2) * self.slope;
        self.differentiated = Some(now);
    }

    fn update(&mut self, now: f64, error: f64, curve: f64) {
        let elapsed = now - self.updated;
        if error == self.error {
            if self.error.abs() <= self.deadband
                && self.differentiated.is_none_or(|t| now - t >= self.sample)
            {
                self.differentiate(now, error, 0.8, 0.6);
            } else {
                let step = (self.error - self.previous).abs();
                if step != 0.0 {
                    let limit = step / elapsed.max(1.0);
                    self.slope = self.slope.clamp(-limit, limit);
                }
            }
            return;
        }
        if elapsed < self.sample {
            return;
        }
        self.integrate(now, error, curve, true);
        self.differentiate(now, error, 0.8, 0.6);
        self.updated = now;
        self.previous = self.error;
        self.error = error;
        self.curve = curve;
    }
}

thread_local! {
    static PID: Cell<Pid> = Cell::new(Pid::default());
    static SNAPSHOT: Cell<[f64; 11]> = const { Cell::new([0.0; 11]) };
}

// Return a consistent snapshot for Home Assistant diagnostics and climate entities.
fn change(f: impl FnOnce(&mut Pid)) -> *const [f64; 11] {
    PID.with(|slot| {
        let mut pid = slot.get();
        f(&mut pid);
        slot.set(pid);
        let [kp, ki, kd] = pid.gains();
        SNAPSHOT.with(|out| {
            out.set([
                pid.error,
                pid.previous,
                pid.updated,
                kp,
                ki,
                kd,
                rounded(kp * pid.error, 3),
                rounded(pid.integral, 3),
                rounded(kd * pid.slope, 3),
                rounded(pid.slope, 3),
                (pid.error.abs() <= pid.deadband) as u8 as f64,
            ]);
            out.as_ptr()
        })
    })
}

#[no_mangle]
pub extern "C" fn pid_init(
    now: f64,
    underfloor: i32,
    gain: f64,
    coefficient: f64,
    weight: f64,
    kp: f64,
    ki: f64,
    kd: f64,
    deadband: f64,
    automatic: i32,
    interval: f64,
    sample: f64,
    version: i32,
) -> *const [f64; 11] {
    change(|p| {
        *p = Pid {
            underfloor: underfloor != 0,
            gain,
            coefficient,
            weight,
            kp,
            ki,
            kd,
            deadband,
            automatic: automatic != 0,
            interval,
            sample,
            version,
            ..Pid::default()
        };
        p.reset(now, 0.0, 0.0);
    })
}

#[no_mangle]
pub extern "C" fn pid_reset(now: f64, error: f64, curve: f64) -> *const [f64; 11] {
    change(|p| p.reset(now, error, curve))
}

#[no_mangle]
pub extern "C" fn pid_restore(now: f64, error: f64, integral: f64, curve: f64) -> *const [f64; 11] {
    change(|p| {
        if !error.is_nan() {
            p.error = error;
            p.previous = error;
        }
        if !integral.is_nan() {
            p.integral = integral;
        }
        if !curve.is_nan() {
            p.curve = curve;
        }
        p.updated = now;
        p.integrated = now;
        p.differentiated = None;
    })
}

#[no_mangle]
pub extern "C" fn pid_update(now: f64, error: f64, curve: f64) -> *const [f64; 11] {
    change(|p| p.update(now, error, curve))
}

#[no_mangle]
pub extern "C" fn pid_integral(now: f64, error: f64, curve: f64, force: i32) -> *const [f64; 11] {
    change(|p| p.integrate(now, error, curve, force != 0))
}

#[no_mangle]
pub extern "C" fn pid_derivative(
    now: f64,
    error: f64,
    alpha1: f64,
    alpha2: f64,
) -> *const [f64; 11] {
    change(|p| p.differentiate(now, error, alpha1, alpha2))
}
