# How SAT works

SAT decides how hot the water going to your radiators or floor should be, then tells the boiler. Three parts work together: a heating curve, room feedback and low-load control.

## Heating curve

The heating curve turns the outside temperature into a water temperature: the colder it is outside, the warmer the water. Once it's as warm outside as your target, the curve stays flat.

The **Heating Curve Coefficient** sets how steep the curve is. When it's right for your home, the room temperature stays close to the target with little help from the room feedback. Raise it if the house stays cold on cold days, lower it if it overheats.

SAT uses one curve with a different offset for underfloor heating. You can play with it here (*a* is the coefficient, *b* the room target):

- [Radiators and heat pumps](https://www.desmos.com/calculator/spfvsid4ds)
- [Underfloor heating](https://www.desmos.com/calculator/i7f7uuyaoz)

> [!TIP]
> To show the graph on a dashboard, add an iframe card:
> ```yaml
> type: iframe
> url: https://www.desmos.com/calculator/spfvsid4ds
> allow_open_top_navigation: true
> allow: fullscreen
> aspect_ratio: 130%
> ```

## Room feedback

The curve gets close, and a PID controller does the rest. It compares the room temperature with the target and raises or lowers the water temperature until they match.

A PID controller needs three settings, called gains. With **Automatic Gains** (on by default), SAT sets them for you:

- The **Adaptive** controller (default) tunes them while it runs.
- The **Classic** and **Improved** controllers make them follow the heating curve, so they get stronger as it gets colder outside.

You can also turn Automatic Gains off and enter your own values in [Options](options.md#general). Only worth it if you know PID tuning.

With several rooms, the [Heating Mode](options.md#general) decides which rooms SAT listens to.

## Low-load control

Every boiler has a lowest power. On mild days the house needs less heat than that, so the boiler overheats the water, shuts off, and starts again a few minutes later. This short-cycling wastes gas and wears out the boiler.

When the water temperature SAT wants is below what the boiler can hold, it switches to planned cycles (Pulse Width Modulation, or PWM): it runs the boiler at a low water temperature for part of each cycle, then lets it rest. The share of time on follows how much heat the house needs. With Automatic duty cycle on (the default), a cycle is an hour divided by the [cycles per hour](options.md#system-configuration), so 15 minutes at 4 per hour.

The low water temperature it runs at is the **overshoot protection value**, which [calibration](#calibration) measures.

The on/off thermostat (switch, valve or input_boolean) always works this way, since it can't set a water temperature.

## Calibration

The overshoot protection value is the water temperature your boiler settles at when it runs at its lowest power. SAT measures it like this:

1. It sets the boiler to its lowest power and asks for 62 °C (radiators), 45 °C (underfloor) or 40 °C (heat pump), capped at your maximum setpoint.
2. It sets your radiator and room thermostats to heat, so the water has somewhere to go.
3. It waits for the water temperature to stop changing, and saves that as the value.
4. It puts your thermostats back the way they were and resumes normal control.

It waits up to 10 minutes for the burner to light and up to 40 minutes for the water temperature to settle. Both can be changed in the [advanced options](options.md#advanced). If calibration fails, SAT tells you why and, on a timeout, the highest temperature it reached.

Run it during setup, or any time with the **Calibrate overshoot protection** button. If it keeps failing, [this discussion](https://github.com/Alexwijn/SAT/discussions/36) explains how to find the value by hand.

## When something goes wrong

With an OpenTherm Gateway, SAT gives control back to the thermostat wired to it when:

- SAT is turned off, the integration is unloaded or Home Assistant stops
- A sensor SAT needs has been missing for 10 minutes

It takes control again once everything is back.

## Further reading

[This discussion post](https://github.com/Alexwijn/SAT/discussions/40) by critictidier looks at SAT in depth, with real observations.

---

Back: [Options](options.md) · See also: [Setup](setup.md)
