# Options

Open the integration page and click **Configure**. Settings are in three menus: [General](#general), [Presets](#presets) and [System Configuration](#system-configuration). Some settings only appear when they apply to your setup.

The defaults suit most homes.

## General

- **PID Controller Version**: Classic, Improved or Adaptive (default). Adaptive tunes itself. Classic and Improved use the Automatic Gains Value below.
- **Heating Mode**: only with rooms added. *Comfort* heats for whichever room is furthest from its target. *Eco* heats only for the main room.
- **Maximum Setpoint**: the hottest water SAT may ask for. 55-75 °C suits radiators, 50 °C underfloor heating. A higher value warms the house up faster. It can't be below the overshoot protection value.
- **Overshoot protection value**: the lowest water temperature your boiler can hold. Set by [calibration](how-it-works.md#calibration), or enter it yourself.
- **Heating Curve Coefficient**: how steep the [heating curve](how-it-works.md#heating-curve) is (default 2.0). Raise it if the house stays cold on cold days, lower it if it overheats.
- **Automatic Gains Value**: how strongly the Classic and Improved controllers react (default 2.0). Setting it equal to the heating curve coefficient works well.
- **Derivative Time Weight**: fine-tunes how SAT damps fast changes (default 2.5).
- **Proportional, Integral, Derivative**: your own PID values, shown when Automatic Gains is off.
- **Minimum Setpoint Controller Version** and **Adjustment Factor for Return Temperature**: settings of [Dynamic Minimum Setpoint](#advanced), shown when it's on. The version picks whether it follows the return or the boiler temperature. Start the factor between 0.1 and 0.5 (default 0.2).
- **Maximum Duty Cycle for Pulse Width Modulation**: the length of a [low-load cycle](how-it-works.md#low-load-control) when Automatic duty cycle is off (default 13 minutes).
- **Contact Sensors**: door and window sensors. When one stays open longer than the minimum open time, SAT switches to the Activity preset, then restores the previous target once it closes.

## Presets

Target temperatures from 5 to 35 °C:

| Preset | Default |
|---|---|
| Activity (also used while a window is open) | 10 °C |
| Away | 10 °C |
| Sleep | 15 °C |
| Home | 18 °C |
| Comfort | 20 °C |

- **Synchronize climates with preset**: applies the Sleep, Away and Activity presets to your room thermostats too.
- **Synchronize with thermostat attached to the boiler**: sends SAT's target to your wall thermostat.

## System Configuration

- **Automatic duty cycle**: SAT picks the [low-load cycle](how-it-works.md#low-load-control) length from the cycles per hour. When off, the length comes from the General menu.
- **Duty Cycles per hour**: the most boiler cycles per hour. 3 or 4 for radiators, 2 or 3 for heat pumps. Underfloor heating uses 4.
- **Synchronize climates with mode**: turning SAT on or off does the same to every attached thermostat.
- **Temperature Sensor maximum value age**: how long a sensor reading counts as fresh (default 6 hours).
- **Mode when nothing can be restored**: whether SAT starts in heat or off when it has no saved state, such as on first start.
- **Minimum time for window to be open**: how long a contact sensor must stay open before SAT reacts (default 15 seconds).

### Advanced

This section is collapsed by default.

- **Thermal Comfort**: controls on the "feels like" temperature (Summer Simmer Index), from temperature and humidity. Needs the humidity sensor.
- **Dynamic Minimum Setpoint (Experimental)**: with some valves closed, the water can come back hotter than expected during low-load control. This watches the return temperature and adjusts the setpoint to match.
- **Force Pulse Width Modulation**: always use [low-load cycles](how-it-works.md#low-load-control), even when the boiler could hold the temperature itself. OTGW only.
- **Minimum Consumption** and **Maximum Consumption**: your boiler's gas use at its lowest and highest power, from its manual. SAT uses them to estimate gas consumption.
- **Climate valve offset**: for room thermostats that don't report whether they're heating. A room counts as wanting heat while it's at least this much below its target (default 0).
- **Target Temperature Step**: the step for SAT's target temperature (default 0.5 °C).
- **Maximum Relative Modulation**: caps the boiler's power, in percent (default 100).
- **Sample Time**: the shortest time between two PID updates (default 1 minute).
- **Calibration flame timeout** and **Calibration plateau timeout**: how long [calibration](how-it-works.md#calibration) waits for the burner to light (default 10 minutes) and for the water temperature to settle (default 40 minutes, at least 5).

---

Back: [Setup](setup.md) · Next: [How SAT works](how-it-works.md)
