# Phase 4 — The real meter — progress

Goal (Plan.md): the Arduino Mega replaces the simulator and real, signed measurements flow end to end.

## Gate: Mega signing spike (CLAUDE.md rule 12) — PASSED 2026-10-19
- `firmware/spike/spike.ino` on the Mega: `SELF_VERIFY:OK`.
- `gateway/verify_test.py` (PyNaCl): real message **True**, tampered **False** → **PASS**. Risk R5 resolved; no HMAC fallback needed.
- Meter public key (stored in EEPROM, reused as the real meter's identity): `25324AF935957C5541E320C9AC0A691FC7042F197ABB65C27BE12805DA2931B9`
- Timings on the 16 MHz Mega: **sign ≈ 6.3 s**, derive public key ≈ 6.3 s (once at boot), verify ≈ 10 s. Free RAM 7,570 bytes (spike uses 601 bytes of globals).

## Steps
| Step | What | Status |
|---|---|---|
| 1 | `firmware/meter/meter.ino`: 1 kHz timer-interrupt sampling (A0 voltage, A1 ACS712), 60 s windows, seq in EEPROM, Ed25519 sign, JSON line out; compiles (28.5 KB flash, 678 B RAM) | Done 2026-10-19 |
| 2 | `gateway/serial_bridge.py` (forward unchanged, log to `gateway/.bridge/readings.jsonl`, `--resend SEQ`) + `tests/test_serial_bridge.py` (5 tests) | Done 2026-10-19 |
| 3 | Golden test: the real Mega's spike signature verifies through `app/verify.py` (2 tests) | Done 2026-10-19 |
| 4 | Flash firmware + run bridge on the real board (needs COM9 free: close the IDE Serial Monitor) | To do (you) |
| 5 | Register the real meter (`VD-0001`, key above) on `/meters` | To do (you) |
| 6 | Wiring (47 kΩ/10 kΩ divider, ACS712, fan + 20–22 Ω 10 W resistor, buck on panel) when parts arrive | Waiting for parts |
| 7 | Calibration at two load points (±5%), constants into `meter.ino` | Waiting for parts |
| 8 | Final `docs/wiring.png` | Waiting for parts |

## Hardware so far
- Arduino Mega 2560 clone with a **CH340** USB chip (needed the WCH CH341SER driver on Windows; COM3 on this laptop is Intel AMT, not the Mega).
- Load: 12 V brushless fan (hydraulic bearing), about 0.1–0.3 A.
- Current sensor: **ACS712-20A** (`ELC-20A`, 100 mV/A, about 49 mA per ADC step). Can't be swapped.
- 1N5819 diode, 2× 10 kΩ, 1× 100 nF ceramic (104).
- Still to buy: 47 kΩ (top of the voltage divider), 20–22 Ω 10 W power resistor, 12 V adapter + barrel-jack adapter, 12 V 5–10 W panel, LM2596 buck module, breadboard, jumpers, hookup wire, multimeter (if needed).

## Design consequences
- **Signing blocks the CPU for about 6.3 s per window.** Sampling must keep running during signing → sample in a timer interrupt (ISR), not in `loop()`. Skip self-verify on the meter (saves about 10 s).
- **ACS712-20A is coarse at fan current** (fan alone ≈ 3 ADC steps). Plan: permanent 20–22 Ω, 10 W resistor in parallel with the fan (total ≈ 0.75 A at 12 V) + heavy oversampling (hundreds of ADC reads per second, averaged). Calibrate at two load points against the multimeter.
- **Window timing:** `tEndMs = tStartMs + 60000` exactly (the timer defines the window), and windows are back to back. Measuring the window with `millis()` could give 59,999 ms, and a perfectly steady load would then fail the `mWh ≤ peak × window` physics rule by rounding.
- **Noise floor:** currents under 30 mA count as zero, so ACS712 noise doesn't mint energy with the load off.
- **Mega serial port:** on this laptop the CH340 shows up as **COM9**.
- Voltage divider: 47 kΩ over 10 kΩ (ratio 5.7) → 21 V becomes ≈ 3.7 V at A0. **Never** 10 kΩ + 10 kΩ (21 V → 10.5 V would damage the Mega).
