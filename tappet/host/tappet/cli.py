"""``tappet`` command line interface."""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

from .client import BodyClient, BodyError

DEFAULT_CALIB = Path(os.environ.get("TAPPET_CALIB", Path.home() / ".tappet" / "calibration.json"))


def _pair(s: str) -> tuple[float, float]:
    a, b = s.split(",")
    return float(a), float(b)


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _connect(args) -> BodyClient:
    try:
        c = BodyClient.connect(args.body, baud=args.baud, heartbeat_s=1.0)
    except (OSError, ValueError) as e:
        sys.exit(f"cannot connect to body at {args.body!r}: {e}\n"
                 "(start the simulator with `tappet sim` or pass --body /dev/ttyUSB0)")
    try:
        c.request("hello", timeout=4.0)
    except BodyError as e:
        sys.exit(f"body did not answer hello: {e}")
    return c


def _print(obj):
    print(json.dumps(obj, indent=2))


def _add_sim_opts(p):
    p.add_argument("--speed", type=float, default=1.0, help="simulation speed factor")
    p.add_argument("--noise", type=float, default=0.0, help="servo noise std (deg)")
    p.add_argument("--backlash", type=float, default=0.0, help="servo backlash (deg, total)")
    p.add_argument("--offset", type=_pair, default=(0.0, 0.0), metavar="DX,DY",
                   help="true screen offset vs. nominal tray (mm)")
    p.add_argument("--rot", type=float, default=0.0, help="true screen rotation (deg)")
    p.add_argument("--seed", type=int, default=0)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_sim(args):
    from .sim import start_sim

    srv = start_sim(port=args.port, host=args.host, speed=args.speed, noise_deg=args.noise,
                    backlash_deg=args.backlash, offset=args.offset, rot_deg=args.rot, seed=args.seed)
    print(f"tappet simulator listening on {args.host}:{srv.port} (speed x{args.speed})", flush=True)
    if args.verbose:
        srv.sim.listeners.append(lambda ev: print("evt", json.dumps(ev)))
        srv.sim.touchscreen.listeners.append(
            lambda ev: print(f"touch {ev['arm']} u={ev['u']:.4f} v={ev['v']:.4f}"))
    print("keys: Enter = BOOT short press, 'l'+Enter = long press, 'b'+Enter = brownout toggle, Ctrl-C quits")
    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                time.sleep(3600)
                continue
            line = line.strip()
            if line == "l":
                srv.sim.press_button("long")
            elif line == "b":
                srv.sim.set_vservo(5.02 if srv.sim.faults else 4.0)
                print("faults:", srv.sim.faults)
            else:
                srv.sim.press_button("short")
                print("enabled:", srv.sim.enabled)
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()


def cmd_bridge(args):
    from .bridge import Bridge, sim_bridge

    kw = dict(port=args.port, pin=args.pin, calib_path=args.calib)
    if args.sim:
        srv, br = sim_bridge(speed=args.speed, noise_deg=args.noise, backlash_deg=args.backlash,
                             offset=args.offset, rot_deg=args.rot, **kw)
    else:
        br = Bridge(args.body, **kw)
    br.start()
    ip = _lan_ip()
    print(f"Tappet bridge on http://{ip}:{br.port}/   (body: {'in-process simulator' if args.sim else args.body})")
    print(f"Target phone touchpad: http://{ip}:{br.port}/touchpad.html")
    print(f"PAIRING PIN: {br.pin}", flush=True)
    br.serve_forever()


def cmd_ping(args):
    c = _connect(args)
    t0 = time.perf_counter()
    r = c.ping()
    rtt = (time.perf_counter() - t0) * 1000
    print(f"pong t={r['t']} ms  rtt={rtt:.1f} ms  {c.desc}")
    _print(c.hello())


def cmd_status(args):
    c = _connect(args)
    _print(c.status())


def cmd_simple(args):
    c = _connect(args)
    if args.command == "enable":
        c.enable()
    elif args.command == "relax":
        c.relax()
    elif args.command == "home":
        c.enable()
        c.home()
    elif args.command == "park":
        c.enable()
        c.park(args.arm)
    elif args.command == "stop":
        c.stop()
    print("ok")


def _load_cal(args):
    from .calibration import load_calibration
    return load_calibration(args.calib)


def cmd_tap(args):
    from .calibration import uv_to_world
    c = _connect(args)
    cal = _load_cal(args)
    if args.uv:
        x, y = uv_to_world(cal, args.a, args.b)
    else:
        x, y = args.a, args.b
    z = args.z if args.z is not None else cal["surface_z"]
    c.enable()
    r = c.tap(x, y, z, arm=args.arm, hold_ms=args.hold)
    print(f"tapped ({x:.1f},{y:.1f},{z:.1f}) with arm {r.get('arm')}")


def cmd_swipe(args):
    from .calibration import uv_to_world
    c = _connect(args)
    cal = _load_cal(args)
    a = [args.a0, args.b0, args.a1, args.b1]
    if args.uv:
        x0, y0 = uv_to_world(cal, a[0], a[1])
        x1, y1 = uv_to_world(cal, a[2], a[3])
    else:
        x0, y0, x1, y1 = a
    z = args.z if args.z is not None else cal["surface_z"]
    c.enable()
    r = c.swipe(x0, y0, x1, y1, z, arm=args.arm, ms=args.ms)
    print(f"swiped ({x0:.1f},{y0:.1f})->({x1:.1f},{y1:.1f}) with arm {r.get('arm')}")


def cmd_neck(args):
    c = _connect(args)
    c.enable()
    c.neck(args.pan, args.tilt, ms=args.ms)
    print("ok")


def _getch():
    """Read one key press (POSIX raw mode; falls back to line input)."""
    try:
        import termios
        import tty
    except ImportError:  # Windows
        return (sys.stdin.readline().strip() or "\n")[0]
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


JOG_HELP = """jog keys:  1/2 arm L/R   a/d -x/+x   w/s +y/-y (away from/toward robot? +y = toward user)
           r/f +z/-z   [ ] step -/+   n/m neck pan -/+   k/j neck tilt +/-
           e enable   x relax   h home   p park   space stop   t tap here   q quit"""


def cmd_jog(args):
    c = _connect(args)
    steps = [0.5, 1, 2, 5, 10, 20]
    si = 3
    arm = args.arm
    pan, tilt = 0.0, 0.0
    print(JOG_HELP)
    while True:
        try:
            x, y, z = c.fk(arm)
            print(f"\rarm {arm} tool=({x:7.1f},{y:7.1f},{z:7.1f}) step {steps[si]:4} mm   ", end="", flush=True)
        except BodyError as e:
            print(f"\r{e}", end="", flush=True)
        ch = _getch()
        d = {"a": (-1, 0, 0), "d": (1, 0, 0), "w": (0, 1, 0), "s": (0, -1, 0),
             "r": (0, 0, 1), "f": (0, 0, -1)}.get(ch)
        try:
            if ch in ("q", "\x03", "\x1b"):
                print()
                return
            if d:
                s = steps[si]
                c.move(arm, x + d[0] * s, y + d[1] * s, z + d[2] * s, timeout=10)
            elif ch in "12":
                arm = "L" if ch == "1" else "R"
            elif ch == "[":
                si = max(0, si - 1)
            elif ch == "]":
                si = min(len(steps) - 1, si + 1)
            elif ch in "nm":
                pan += -5 if ch == "n" else 5
                c.neck(pan=pan, timeout=5)
            elif ch in "kj":
                tilt += 5 if ch == "k" else -5
                c.neck(tilt=tilt, timeout=5)
            elif ch == "e":
                c.enable()
            elif ch == "x":
                c.relax()
            elif ch == "h":
                c.home()
            elif ch == "p":
                c.park("both")
            elif ch == " ":
                c.stop()
            elif ch == "t":
                cal = _load_cal(args)
                c.tap(x, y, cal["surface_z"], arm=arm)
        except BodyError as e:
            print(f"\n! {e}")


def cmd_calib(args):
    """Servo trim wizard: find s_zero for each joint with servo_raw, then calib_set."""
    from .kinematics import Kinematics
    c = _connect(args)
    cfg_joints = c.calib_get()
    kin = Kinematics({"joints": cfg_joints, "servo": {"us_min": 500, "us_max": 2500, "deg_range": 180},
                      "arms": {}, "linkage": {}})
    names = args.joint or list(cfg_joints)
    c.enable()
    print("Servo trim wizard. For each joint, adjust the pulse until the joint sits exactly at its\n"
          "'neutral' angle (see docs/ calibration guide), then type 'ok'.  Commands: +N / -N (µs),\n"
          "a number (absolute µs), ok, skip, quit.")
    new = {}
    for n in names:
        j = cfg_joints[n]
        us = kin.joint_to_us(n, j["neutral"])
        print(f"\n{n} (ch {j['ch']}): bring to neutral = {j['neutral']}° ; current s_zero={j['s_zero']}")
        while True:
            try:
                c.servo_raw(int(j["ch"]), us)
            except BodyError as e:
                print("!", e)
            s = input(f"  us={us:.0f} > ").strip()
            if s in ("q", "quit"):
                s = "quit"
                break
            if s == "skip":
                break
            if s == "ok":
                s_zero = round(kin.us_to_servo(us), 2)
                new[n] = {"s_zero": s_zero}
                print(f"  {n}.s_zero = {s_zero}")
                break
            try:
                if s.startswith(("+", "-")):
                    us += float(s)
                else:
                    us = float(s)
                us = min(max(us, 500), 2500)
            except ValueError:
                print("  ?")
        if s == "quit":
            break
    if new:
        save = input(f"save {len(new)} trims to NVS? [Y/n] ").strip().lower() != "n"
        c.calib_set(new, save=save)
        print(json.dumps(new, indent=2))
    c.home()


def _touch_setup(args):
    """Return (client, touch_log, cleanup) for autocal/taptest: in-process sim or real body plus an
    HTTP touchpad receiver (the bridge)."""
    if args.sim:
        from .bridge import sim_bridge
        srv, br = sim_bridge(port=0, speed=args.speed, noise_deg=args.noise, backlash_deg=args.backlash,
                             offset=args.offset, rot_deg=args.rot, calib_path=args.calib, log=lambda s: None)
        br.start()

        def cleanup():
            br.close()
            srv.close()
        return br, cleanup
    from .bridge import Bridge
    br = Bridge(args.body, port=args.http_port, calib_path=args.calib, log=lambda s: None)
    br.start()
    if br.client is None:
        sys.exit(f"cannot connect to body {args.body}: {br.link_error}")
    ip = _lan_ip()
    print(f"Open http://{ip}:{br.port}/touchpad.html full-screen on the target phone, then touch it once.")
    print(f"(remote UI PIN: {br.pin})")
    mark = br.touch_log.last_seq
    while br.touch_log.wait_for(mark, 1.0) is None:
        pass
    print("touchpad connected.")
    time.sleep(1.0)
    return br, br.close


def cmd_autocal(args):
    from .calibration import auto_calibrate, save_calibration
    br, cleanup = _touch_setup(args)
    try:
        c = br.body()
        c.enable()
        res = auto_calibrate(c, br.touch_log, br.calibration, arm=args.arm, n=args.grid, log=print)
        save_calibration(res["calibration"], args.calib)
        print(f"fit rms {res['rms_mm']:.3f} mm; previous calibration max error {res['max_mm']:.2f} mm")
        print(f"saved {args.calib}")
        _print(res["calibration"]["H_sw"])
    finally:
        cleanup()


def cmd_taptest(args):
    from .calibration import auto_calibrate
    from .taptest import run_taptest, summary_text, write_report
    from .workspace import out_dir
    br, cleanup = _touch_setup(args)
    try:
        c = br.body()
        c.enable()
        cal = br.calibration
        if args.autocal:
            print("auto-calibrating first ...")
            cal = auto_calibrate(c, br.touch_log, cal, log=print)["calibration"]
        res = run_taptest(c, br.touch_log, cal, n=args.n, arm=args.arm, seed=args.seed,
                          log=print if args.verbose else None, clock=br.clock)
        csv_p, json_p = write_report(res, args.out or out_dir())
        print(summary_text(res))
        print(f"wrote {csv_p}\n      {json_p}")
        if args.min_rate is not None and res["registration_rate"] < args.min_rate:
            sys.exit(1)
    finally:
        cleanup()


def cmd_workspace(args):
    from . import workspace
    s = workspace.run(out=args.out)
    c = s["coverage_pct"]
    print(f"screen reachability (tap z {s['z_press']:.1f}..{s['z_approach']:.1f} mm, grid {s['grid']}):")
    print(f"  L {c['L']:.1f}%   R {c['R']:.1f}%   either {c['either']:.1f}%   both {c['both']:.1f}%")
    if s["unreachable_uv"]:
        print(f"  {len(s['unreachable_uv'])} unreachable grid points, e.g. {s['unreachable_uv'][:5]}")
    for f in s["files"]:
        print("wrote", f)


def cmd_gen_vectors(args):
    from .config import find_config_path
    from .kinematics import dump_vectors, generate_vectors
    path = Path(args.out) if args.out else find_config_path().parent / "kinematics_vectors.json"
    text = dump_vectors(generate_vectors())
    if args.check:
        old = path.read_text() if path.exists() else ""
        if old == text:
            print(f"{path} is up to date")
            return
        sys.exit(f"{path} differs from generated vectors")
    path.write_text(text)
    print(f"wrote {path}")


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tappet", description="Tappet robot host tools")
    p.add_argument("--body", default=os.environ.get("TAPPET_BODY", "sim"),
                   help="body link: tcp://host:port, sim (=tcp://127.0.0.1:7777), /dev/ttyUSB0, COM3 "
                        "(env TAPPET_BODY)")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--calib", type=Path, default=DEFAULT_CALIB, help="screen calibration JSON path")
    common = argparse.ArgumentParser(add_help=False)  # same options accepted after the subcommand
    common.add_argument("--body", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--baud", type=int, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--calib", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)
    _orig = sub.add_parser

    def add_parser(name, **kw):
        kw.setdefault("parents", [common])
        return _orig(name, **kw)
    sub.add_parser = add_parser

    s = sub.add_parser("sim", help="run the firmware simulator (TCP)")
    s.add_argument("--port", type=int, default=7777)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("-v", "--verbose", action="store_true")
    _add_sim_opts(s)
    s.set_defaults(fn=cmd_sim)

    s = sub.add_parser("bridge", help="serve the web UI + head API from this PC")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--pin", default=None)
    s.add_argument("--sim", action="store_true", help="use an in-process simulator as the body")
    _add_sim_opts(s)
    s.set_defaults(fn=cmd_bridge)

    for name, fn, hlp in (("ping", cmd_ping, "ping + hello"), ("status", cmd_status, "body status")):
        sub.add_parser(name, help=hlp).set_defaults(fn=fn)
    for name in ("enable", "relax", "home", "stop"):
        sub.add_parser(name, help=f"{name} the body").set_defaults(fn=cmd_simple)
    s = sub.add_parser("park", help="park arms")
    s.add_argument("arm", nargs="?", default="both", choices=["L", "R", "both"])
    s.set_defaults(fn=cmd_simple)

    s = sub.add_parser("jog", help="interactive keyboard jog")
    s.add_argument("--arm", default="R", choices=["L", "R"])
    s.set_defaults(fn=cmd_jog)

    s = sub.add_parser("tap", help="tap at world x y [z] (mm) or --uv u v")
    s.add_argument("a", type=float)
    s.add_argument("b", type=float)
    s.add_argument("z", type=float, nargs="?")
    s.add_argument("--uv", action="store_true", help="a b are screen-normalized u v")
    s.add_argument("--arm", default="auto", choices=["auto", "L", "R"])
    s.add_argument("--hold", type=float, default=None, help="hold ms")
    s.set_defaults(fn=cmd_tap)

    s = sub.add_parser("swipe", help="swipe x0 y0 x1 y1 [z] (mm) or --uv")
    for k in ("a0", "b0", "a1", "b1"):
        s.add_argument(k, type=float)
    s.add_argument("z", type=float, nargs="?")
    s.add_argument("--uv", action="store_true")
    s.add_argument("--arm", default="auto", choices=["auto", "L", "R"])
    s.add_argument("--ms", type=float, default=None)
    s.set_defaults(fn=cmd_swipe)

    s = sub.add_parser("neck", help="move the neck")
    s.add_argument("pan", type=float)
    s.add_argument("tilt", type=float)
    s.add_argument("--ms", type=float, default=None)
    s.set_defaults(fn=cmd_neck)

    s = sub.add_parser("calib", help="servo trim wizard (servo_raw + calib_set)")
    s.add_argument("--joint", action="append", help="joint name(s) to trim (default all)")
    s.set_defaults(fn=cmd_calib)

    for name, fn, hlp in (("autocal", cmd_autocal, "touch-probe screen auto-calibration"),
                          ("taptest", cmd_taptest, "v0 acceptance tap test")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("--sim", action="store_true", help="run against an in-process simulator")
        s.add_argument("--http-port", type=int, default=8080, help="touchpad receiver port (real body)")
        s.add_argument("--arm", default="auto", choices=["auto", "L", "R"])
        _add_sim_opts(s)
        if name == "autocal":
            s.add_argument("--grid", type=int, default=3)
        else:
            s.add_argument("-n", type=int, default=100)
            s.add_argument("--autocal", action="store_true", help="auto-calibrate before testing")
            s.add_argument("--out", default=None)
            s.add_argument("--min-rate", type=float, default=None, help="exit 1 below this registration rate")
            s.add_argument("-v", "--verbose", action="store_true")
        s.set_defaults(fn=fn)

    s = sub.add_parser("workspace", help="screen reachability per arm + plot (host/out/)")
    s.add_argument("--out", default=None)
    s.set_defaults(fn=cmd_workspace)

    s = sub.add_parser("gen-vectors", help="regenerate config/kinematics_vectors.json")
    s.add_argument("--check", action="store_true", help="only verify the file is up to date")
    s.add_argument("--out", default=None)
    s.set_defaults(fn=cmd_gen_vectors)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except BodyError as e:
        sys.exit(f"error: {e}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
