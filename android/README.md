# Tappet head — Android app

The Android phone mounted on Tappet's neck is the robot's **head**: animated face, front camera,
microphone, speaker, Wi-Fi web server, and the Claude agent. It talks to the ESP32-S3 body over USB
(Android Open Accessory) using the JSON-lines protocol of [`docs/SPEC.md`](../docs/SPEC.md) §4.

Package `dev.tappet.head` · Kotlin 2.0 · Jetpack Compose · minSdk 26 · target/compileSdk 35 · AGP 8.7 · Gradle 8.11.1 wrapper.

## Architecture

```
MainActivity (Compose, fullscreen, keep-screen-on)
  └─ observes HeadService.controller ───────────────────────────────┐
HeadService (foreground: connectedDevice | camera | microphone)       │
  └─ HeadController  (survives activity recreation)                   ▼
       ├─ BodyClient ── BodyLink ─┬─ AoaTransport       USB accessory "Tappet"/"TappetBody" (production)
       │   JSON lines, ids,       ├─ UsbSerialTransport OTG → DevKit UART / ESP32-S3 USB-CDC, 115200 8N1
       │   awaitDone, events,     └─ TcpTransport       Python simulator host:7777 (emulator: 10.0.2.2)
       │   1.5 s ping, reconnect
       ├─ CalibrationStore (screen_calibration.json) ── ScreenCalibration: H_sw (u,v→x,y mm), surface_z,
       │                                                H_ic (camera px→u,v), camera_size
       ├─ RobotActions  tap / double tap / long press / swipe / typeText (KeyboardLayout) / neck /
       │                lookAtDevice / lookAtUser / jog / autoCalibrate / tapTest   (serialized by a mutex)
       ├─ TouchLog      touches reported by touchpad.html (POST /api/touchpad/event)
       ├─ CameraController  CameraX ImageAnalysis (front camera) → latest frame, JPEG, rectified screen
       │                    (Matrix.setPolyToPoly), motion centroid → eye gaze
       ├─ Speaker (TextToSpeech, word ticks → mouth) · VoiceListener (SpeechRecognizer, "hey tappet", push-to-talk)
       ├─ Agent  Claude Messages API tool loop (OkHttp + kotlinx.serialization) + AgentTools
       │         (tap, double_tap, long_press, swipe, type_text, look_at_device [image], look_at_user,
       │          move_head, set_timer, add_reminder [AlarmManager + notification], add_task, list_tasks,
       │          complete_task, remember, recall [memory.json], get_time)
       └─ WebServer (NanoHTTPD :8080)  SPEC §6 API + static web UI, pairing PIN → token
```

Source layout (`app/src/main/java/dev/tappet/head/`):

| package | contents |
|---|---|
| `config` | `RobotConfig` (parses `robot.json`), device profiles, `SettingsStore` |
| `kinematics` | `Kinematics` — exact port of SPEC §3 FK/IK + limit checks + `auto` arm choice |
| `body` | `BodyLink` + transports, `BodyClient`, USB permission helper |
| `calib` | `Homography` (normalized DLT, Jacobi eigen-solver), `ScreenCalibration`, `CalibrationStore`, `TouchLog` |
| `robot` | `RobotActions`, `KeyboardLayout` (iPhone portrait QWERTY, 123 / #+= layers, shift + auto-cap model) |
| `camera` | `CameraController` |
| `voice` | `Speaker`, `VoiceListener`, `WakePhrase` |
| `agent` | `AnthropicApi`, `Agent`, `AgentTools`, `TaskStore`/`MemoryStore`, `Reminders` |
| `web` | `WebServer`, `Pairing` |
| `service` | `HeadService`, `HeadController` |
| `ui` | `MainActivity`, `RobotFace`, overlay/chat, settings, calibration, manual control, QR |

### Shared assets (web UI and robot.json)

`app/build.gradle.kts` has a `syncSharedAssets` task (run before every build) that copies

* `../web/**` → APK `assets/web/…` — served by the web server at `/` (e.g. `/index.html`, `/touchpad.html`)
* `../config/robot.json` → APK `assets/config/robot.json` — the single source of truth for geometry

into `app/build/generated/tappetAssets`, which is added to the main `assets` source set. So the web UI
lives under a **`web/` prefix** in the APK (not at the asset root). The repo's `web/` is never modified.

### Web API

Implements every endpoint of SPEC §6 with the same JSON shapes and HTTP status codes as the Python bridge
(`host/tappet/bridge.py`), so the shared web UI works against either host: `/api/pair`, `/api/status`,
`/api/cmd` (always 200, raw body response), `/api/tap`, `/api/swipe`, `/api/type`, `/api/neck`, `/api/jog`,
`/api/camera.jpg[?rectified=1]`, `/api/say`, `/api/chat`, `GET/POST /api/calibration` (partial merge,
`camera_corners`+`camera_size`, `{"reset":true}`), `/api/calibrate/auto`, `/api/touchpad/event` (no token),
`/api/touchpad/events?since=`, `/api/taptest`. Errors are `{ok:false, err, msg}` with
`unreachable/limit/bad_args → 400`, `unauthorized → 401`, `bad_pin → 403`, `not_enabled → 409`,
`no_touch → 422`, `not_implemented → 501` (e.g. chat without an API key), `link_down → 503`, `timeout → 504`.

Auth: the 6-digit PIN shown on the face (and in the notification) is exchanged for a token
(`POST /api/pair {"pin":"123456"}` → `{"token"}`); send it as `X-Tappet-Token` or `?token=`. A PIN is
single-use (a new one appears after each pairing); 5 wrong PINs lock pairing for 30 s. Tokens persist
(hashed) until “Revoke pairings” in Settings.

## Build

Requirements: JDK 17, Android SDK with platform 35 and build-tools 35.

```sh
cd android
echo "sdk.dir=/path/to/android/sdk" > local.properties    # git-ignored
export JAVA_HOME=/path/to/jdk17
./gradlew :app:assembleDebug :app:testDebugUnitTest
```

APK: `app/build/outputs/apk/debug/app-debug.apk`.

Unit tests (JVM): `KinematicsTest` (all vectors in `../config/kinematics_vectors.json`, read via a relative
path from `android/app`), `HomographyTest` (exact/least-squares DLT, inverse, default screen orientation,
camera corners, bridge-compatible merge), `KeyboardLayoutTest`, `BodyClientTest` (fake body: requests,
`not_enabled`, `done`/`error` events, raw requests, heartbeat; wake phrase).

## Install & permissions

```sh
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

On first launch grant **Camera** (screen images, eye tracking), **Microphone** (voice) and
**Notifications** (timers/reminders, Android 13+). The app runs a foreground service (types
`connectedDevice|camera|microphone`) so the body link, web server and voice keep running with the activity
in the background. Exact alarms (`SCHEDULE_EXACT_ALARM`) are used when allowed, otherwise inexact alarms.
Disable battery optimisation for the app on aggressive OEM ROMs. Alarms set by the agent do not survive a
reboot of the head phone.

### Docking on the body (AOA)

Plug the head into the body's “HEAD” USB-C port. The ESP32-S3 switches the phone into accessory mode
(manufacturer `Tappet`, model `TappetBody`, `res/xml/accessory_filter.xml`), Android offers to open Tappet
(tick “always”), and the app connects automatically. The body also charges the phone.

### Alternative: USB serial (OTG)

Settings → Body link → *USB serial*: connect the phone (OTG adapter) to the DevKit's UART port
(CP210x/CH34x/FTDI) or the ESP32-S3 native USB port; 115200 8N1. DTR/RTS are left de-asserted so the
ESP32 is not reset. *Auto* tries AOA → USB serial → simulator.

## Pairing an iPhone (remote control + touch-probe calibration)

1. Put the head and the iPhone on the same Wi-Fi.
2. Tap the face → the overlay shows the URL (`http://<head-ip>:8080`), a QR code and the PIN.
3. On the iPhone, scan the QR code (Camera app) → open the web UI → enter the PIN.
4. For calibration, open `http://<head-ip>:8080/touchpad.html` on the **target** iPhone (Add to Home
   Screen for real full-screen), lay it in the tray, then run *Calibrate → Auto calibration* (web UI or the
   head's Calibration screen). The robot probes the surface height at the screen centre, taps a 3×3 grid,
   fits `H_sw` from the reported touches and saves it. Then run the 100-tap accuracy test.
5. Camera rectification: tilt the head to the tray, take a snapshot and tap the four screen corners
   (TL, TR, BR, BL) — on the head's Calibration screen or in the web UI. This stores `H_ic` +
   `camera_size`; `look_at_device` and `/api/camera.jpg?rectified=1` then return an upright screen image.

## Agent

Settings → Anthropic API key (stored in app-private SharedPreferences) and model (default
`claude-sonnet-5`). Talk with “hey tappet, …” (or say “hey tappet”, wait for the beep, then speak), the
*Talk* push-to-talk button, the overlay chat box, or the web UI chat. Replies are spoken with TTS while the
mouth animates. History is append-only per conversation and restarts after 30 min idle or 60 messages;
durable facts go through `remember`/`recall` (`files/memory.json`); tasks live in `files/tasks.json`.

## Development with the simulator (TcpTransport)

```sh
# on the PC (repo root)
cd host && uv run tappet sim            # body simulator on :7777 (see host/README.md)
```

In the Android emulator: Settings → Body link → *Simulator (TCP)*, host `10.0.2.2`, port `7777`.
On a real phone use the PC's LAN IP. With the emulator, forward the web server to the PC with
`adb forward tcp:8080 tcp:8080` and open `http://localhost:8080`.

## Deviations / notes

* `GET /api/status` follows the Python bridge's shape (`body` is the body `status` response itself, plus
  `agent.available`, `camera.available`, `touchpad.last_seq`) rather than the literal `body:{status}` of
  the SPEC table, so the shared web UI works unchanged.
* Auto calibration first probes `surface_z` (lowers z in 1 mm steps from `surface_z + 1` until the
  centre tap registers) before the 3×3 grid; the Python bridge keeps `surface_z` unchanged.
* Motions that get `not_enabled` trigger one `enable` and a retry (the agent should not have to know).
* Keyboard typing models iOS auto-capitalization and the 123-layer returning to letters after a space;
  both are switchable in Settings if a keyboard behaves differently. Emoji/accented characters are
  skipped and reported.
