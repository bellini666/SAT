# Smart Autotune Thermostat v2

[![hacs][hacs-badge]][hacs-url]
[![release][release-badge]][release-url]
![build][build-badge]

SAT is a smart thermostat for Home Assistant that controls your boiler.

A regular thermostat turns the boiler on and off, and the boiler heats its water to a fixed, often high, temperature. SAT works out the lowest water temperature that keeps your home at the target, from the outside temperature and how far each room is from its target, and sends that to the boiler. Cooler water means less gas and fewer on/off cycles.

SAT tunes itself: you pick your sensors and heating system, and it does the rest.

> [!NOTE]
> SAT v2 is a fork of the awesome original [Smart Autotune Thermostat](https://github.com/Alexwijn/SAT) by [Alex Wijnholds](https://github.com/Alexwijn), with many fixes and improvements. See [what's different][differences].

## Features

- 🔥 One room or many: SAT can follow your TRVs and room thermostats
- 🏠 Presets: Home, Comfort, Sleep, Away and Activity
- 🪟 Turns the heating down while a window or door is open
- 🚿 Hot water temperature control (OpenTherm and EMS-ESP)
- 🚨 Boiler fault sensors and an alert when heating doesn't behave (OpenTherm and EMS-ESP)
- 🩺 Diagnostics download from the integration page

## How it works

- **Heating curve**: the colder it is outside, the warmer the water SAT asks for.
- **Room feedback**: SAT nudges that temperature up or down until the room reaches its target.
- **Low-load control**: on mild days the house needs less heat than the boiler gives at its lowest power. SAT then runs the boiler in planned on/off cycles, so it doesn't short-cycle.
- **Calibration**: SAT measures the lowest water temperature your boiler can hold, so low-load control fits your boiler.

More in [How SAT works][how-it-works].

## What you need

- A boiler that Home Assistant can talk to through one of:
  - [OpenTherm Gateway (OTGW)](https://otgw.tclcode.com/), over MQTT or serial
  - [ESPHome OpenTherm](https://esphome.io/components/opentherm.html)
  - [EMS-ESP](https://emsesp.org/), over MQTT
  - OpenTherm adapters such as the [DIYLess](https://diyless.com/) Master OpenTherm Shield, [Ihor Melnyk's](http://ihormelnyk.com/opentherm_adapter) adapter or [Jiří Praus'](https://www.tindie.com/products/jiripraus/opentherm-gateway-arduino-shield/) Arduino shield
- Or any `switch`, `valve` or `input_boolean` that turns your heating on and off. SAT then works as a smart on/off thermostat.
- A room temperature sensor and an outside temperature sensor or weather entity.

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bellini666&repository=SAT&category=integration)

Or add `https://github.com/bellini666/SAT` to HACS as a custom repository of type "Integration" and download "Smart Autotune Thermostat v2".

Without HACS, copy `custom_components/sat` from the [latest release][release-url] into your `custom_components` folder.

Restart Home Assistant, then add "Smart Autotune Thermostat" under **Settings → Devices & services**. The [setup guide][setup] walks through each step.

## Documentation

- [Setup][setup]: what each setup step asks for
- [Options][options]: every setting, tab by tab
- [How SAT works][how-it-works]: heating curve, room feedback, low-load control and calibration
- [Differences from the original SAT][differences]

<!-- Badges -->

[hacs-url]: https://hacs.xyz/docs/faq/custom_repositories/
[hacs-badge]: https://img.shields.io/badge/hacs-custom-orange.svg?style=for-the-badge
[release-badge]: https://img.shields.io/github/v/release/bellini666/SAT?style=for-the-badge
[build-badge]: https://img.shields.io/github/actions/workflow/status/bellini666/SAT/pytest.yml?branch=main&style=for-the-badge

<!-- References -->

[release-url]: https://github.com/bellini666/SAT/releases
[setup]: https://github.com/bellini666/SAT/blob/main/docs/setup.md
[options]: https://github.com/bellini666/SAT/blob/main/docs/options.md
[how-it-works]: https://github.com/bellini666/SAT/blob/main/docs/how-it-works.md
[differences]: https://github.com/bellini666/SAT/blob/main/docs/differences.md
