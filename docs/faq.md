# Tappet — FAQ

**What is Tappet?**
A ~$99 desk robot. A spare Android phone is its head (face, camera, voice, brain), and two small
arms with stylus tips tap, swipe and type on your other phone lying in a tray in front of it. You
talk to it, and it uses the phone the way you would.

**Why use a robot? Why not just automate the phone in software?**
iPhones don't allow one app to drive another, and many apps block accessibility automation. A
physical finger works on any app and needs nothing installed on the target phone.

**Which phones work?**
*Target phone:* any capacitive touchscreen phone that fits the tray, default iPhone 15/16 (6.1"),
with shims for others. *Head phone:* an Android phone with Android Open Accessory support, which
most phones from 2018 on have. See the compatibility matrix in [test-plan.md](test-plan.md).

**How accurate is it?**
About 1 mm (RSS) after calibration, against a 2 mm requirement. Uncalibrated, it's about 1.5 mm
at the screen centre and 1.7 mm at the far edge. Taps are checked with a 100-point tap test. Details
in [kinematics.md](kinematics.md).

**Can one arm reach the whole screen?**
Yes. Each arm alone covers the entire default screen (1,323 checked poses, no failures). The second
arm handles the near side faster and allows two-finger gestures.

**Why hobby servos rather than steppers or encoders?**
Cost. Positional SG90/MG90S servos plus calibration and a 1.5:1 gear on the yaw axis reach the
accuracy target for about $10 of servos at volume. See [ADR-0002](decisions/0002-positional-servos-yaw-gearing.md).

**Does the head phone's battery drain?**
No. The robot charges the head over the same USB cable it uses for data (Android Open Accessory).
See [ADR-0004](decisions/0004-usb-aoa-link.md).

**What charger do I need?**
A USB-C PD charger of at least 20 W that offers 9 V. A 5 V-only charger is detected and the robot
refuses to move the arms. The assembled version includes a charger. See [ADR-0007](decisions/0007-power-usb-c-pd.md).

**Does it need the internet?**
The voice agent uses the Claude API with your own API key. Without internet, the web remote control,
timers and tasks still work.

**Can it buy things or send messages by itself?**
Only when you ask it to, and it must ask you for confirmation before payments, purchases, sending
messages or deleting things. See [safety.md](safety.md).

**Can it hurt someone or damage my phone?**
It's very low energy: about 0.4 N press force (a light finger tap), 9 V max, no battery. Only a soft
conductive tip touches the screen. If anything goes wrong, it parks and goes limp.

**How long does the kit take to build?**
About 2 hours the first time, plus ~15 minutes of calibration. See [assembly.md](assembly.md).

**Is it open source?**
Yes. Hardware, firmware, the Android app and host tools are in this repository under the
[LICENSE](../LICENSE).

**What does it cost to make?**
At 1,000 units: $39.22 BOM for the kit and $46.32 for the assembled version. See [bom.md](bom.md) and
[business.md](business.md).

**Can I control it without voice?**
Yes. Open `http://<head-ip>:8080` on any browser in the same network, enter the PIN shown on the
robot's face, and tap on a live view of the screen. There's also a JSON protocol over USB serial
for scripts ([SPEC.md §4](SPEC.md)).
