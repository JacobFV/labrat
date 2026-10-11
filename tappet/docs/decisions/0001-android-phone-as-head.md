# ADR-0001: Use a user-supplied Android phone as the head

* Status: Accepted
* Date: 2026-10-11

## Context

A desk robot that operates a phone needs a display (face), camera (to see the target screen),
mic and speaker (voice), networking, and enough compute to run an LLM agent client. Building these
in would add roughly $40–80 to the BOM (camera module, display, audio, SoC or Linux SBC) and put the
retail price far above the $99 target.

## Decision

The head is the user's spare **Android phone**, held on a 2-axis (pan/tilt) neck and running the
Tappet app. The body (ESP32-S3 + servos) has no camera, display or audio.

## Alternatives considered

* **Raspberry Pi + camera + small display:** ~$60–90 extra at volume, plus supply risk; worse
  camera and display than a 5-year-old phone.
* **iPhone as head:** no USB accessory path to a microcontroller without MFi, and iOS limits
  background operation. Rejected.
* **ESP32-S3 camera module only, everything else in the cloud:** poor image quality for reading a
  phone screen, no face, and a cloud dependency for every frame.

## Consequences

* BOM stays at $39.22 (kit, 1k). Users who don't own a spare Android phone need one
  (~$40–60 used).
* The app must handle a wide range of Android versions and vendors, so a compatibility matrix is
  needed ([test-plan.md](../test-plan.md)).
* The head mass (≤ 220 g) sets the neck tilt servo (MG90S) and the base ballast.
