#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
rustc +1.95.0 rust/control.rs --edition=2021 --crate-type=cdylib \
    --target wasm32-unknown-unknown -C opt-level=s -C panic=abort \
    -C strip=symbols -C link-arg=-zstack-size=16384 -o custom_components/sat/control.wasm
chmod 644 custom_components/sat/control.wasm
