# Tappet — Business Model

## Pitch

Most people have an old Android phone in a drawer. Tappet turns it into the face and brain of a
desk robot that can **physically use your other phone** for you. It taps, swipes and types on any
app, with no API, jailbreak or accessibility permission needed. Ask "read me the GitHub 2FA code"
or "reorder my usual coffee" and the agent looks at the screen through the head camera and taps
through the app.

Because the user supplies the phone (screen, camera, mic, speaker, compute, networking), the robot
itself is just servos, a microcontroller and plastic. That keeps the price at **$99**.

## Customers

| Segment | Why they buy | Notes |
|---|---|---|
| Tinkerers / makers (launch) | Open hardware, hackable agent, fun desk robot | Kit SKU; community builds content |
| Developers & QA | Physical "real finger" testing of iOS apps, which no software tool can do on a stock iPhone | Taptest + API are the product here; possible pro tier |
| Accessibility | Hands-free phone operation by voice | Needs care in claims; partner with OT/AT groups before marketing to this group |
| Creators | A charismatic robot that uses a phone on camera | Marketing channel more than revenue |

## SKUs and unit economics (at 1k units)

| | **Kit — $99** | **Assembled — $129** |
|---|---|---|
| BOM, before freight ([bom.md](bom.md)) | $39.22 | $46.32 |
| Landed (+10% freight/duty) | $43.14 | $50.95 |
| Payment fees (2.9% + $0.30) | $3.17 | $4.04 |
| Outbound shipping (US avg) | $12.00 | $12.00 |
| Returns / warranty reserve (4% of price) | $3.96 | $5.16 |
| **Contribution per unit** | **≈ $36.7 (37%)** | **≈ $56.9 (44%)** |

Not included above: customer acquisition, support, tooling and NRE. A single prototype kit costs
**$78.86** in parts, so pre-volume batches (< 100) roughly break even at $99. Early batches should
be sold as a crowdfunding reward at a higher "founder" price or as the assembled SKU.

### Sensitivities

* **Injection moulding** the printed parts saves ~$9/unit, which pays back $20k of tooling at ~2.2k units.
* **Shipping** is the largest cost after BOM. Free-shipping thresholds or regional 3PLs move
  contribution by ±$4.
* **Software subscription (optional, later):** the agent uses the user's own Claude API key at
  launch. A hosted plan (~$5/mo, including model usage and skills like app-specific
  routines) would be margin on top of hardware, not a requirement.

## Go-to-market

1. **Open source from day one** (hardware + firmware + app, MIT; see [LICENSE](../LICENSE)).
   Demo videos of the robot using an iPhone are the main acquisition channel.
2. **Crowdfunding** for the first 500–1,000 units. That funds the first PCB run and print-farm
   batch and validates the price.
3. **Direct web store** after that. Wholesale/retail only after moulded parts bring the BOM down.

## Competition / alternatives

| Alternative | Gap Tappet fills |
|---|---|
| Phone-automation software (Shortcuts, Tasker, accessibility agents) | Restricted on iOS; per-app integrations; Tappet works on any app with nothing installed on the target phone beyond a browser page for calibration |
| Lab/QA tapping robots (e.g. industrial SCARA-based test rigs) | $5k–$50k; Tappet is ~1% of the cost at lower accuracy |
| Desk companion robots | Don't do useful work; Tappet's useful work is the hook |

## Key risks

| Risk | Mitigation |
|---|---|
| Accuracy is not good enough for small targets | Error budget ≈ 1.0 mm RSS after calibration ([kinematics.md](kinematics.md)); tap test gates every shipped unit |
| Android AOA compatibility varies by phone | Compatibility matrix in [test-plan.md](test-plan.md); USB-serial and Wi-Fi fallbacks |
| Users without a spare Android phone | List known-good $40–60 used phones; future: bundle option |
| Platform terms (automating apps) | Robot acts like a human finger at human speed; no ToS-circumventing software on the device |
| Safety / compliance cost | No battery, ≤ 9 V; FCC/CE via pre-certified ESP32 module ([safety.md](safety.md)) |
