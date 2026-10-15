"""python -m labrat_bridge --server wss://HOST --token labrat [--mock all|so_a,cnc,hub] [--ports auto]
python -m labrat_bridge calibrate --device so_a [--port /dev/cu.usbmodemXXX]
python -m labrat_bridge hub [--port /dev/ttyUSB0|mock] ['{"cmd":"hello"}' ...]   (bench console for the servo hub)
python -m labrat_bridge scan"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from .bridge import ALL_IDS, Bridge, bridge_url, build_devices, discover, print_table


def parse_mock(s: str | None) -> set[str]:
    if not s:
        return set()
    if s.strip() == "all":
        return set(ALL_IDS)
    ids = {x.strip() for x in s.split(",") if x.strip()}
    bad = ids - set(ALL_IDS)
    if bad:
        sys.exit(f"unknown device ids in --mock: {sorted(bad)} (valid: {ALL_IDS})")
    return ids


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    logging.basicConfig(level=os.environ.get("LOG", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    if argv and argv[0] == "calibrate":
        ap = argparse.ArgumentParser(prog="labrat_bridge calibrate")
        ap.add_argument("--device", default="so_a", choices=["so_a", "so_b"])
        ap.add_argument("--port", help="Feetech bus adapter port (omit = mock dry-run)")
        ap.add_argument("--step", help="non-interactive: zero | limits:<secs> | pose:<home|ready|rest>")
        a = ap.parse_args(argv[1:])
        from .so101 import calibrate, calibrate_step

        if a.step:
            if not a.port:
                ap.error("--step needs --port")
            calibrate_step(a.device, a.port, a.step)
        else:
            calibrate(a.device, a.port, mock=a.port is None)
        return
    if argv and argv[0] == "teleop":
        ap = argparse.ArgumentParser(prog="labrat_bridge teleop", description="Leader arm (moved by hand) drives the follower.")
        ap.add_argument("--leader", default="so_b")
        ap.add_argument("--follower", default="so_a")
        ap.add_argument("--leader-port", required=True)
        ap.add_argument("--follower-port", required=True)
        ap.add_argument("--hz", type=float, default=30.0)
        ap.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = until Ctrl-C)")
        a = ap.parse_args(argv[1:])
        import time as _t
        from .so101 import So101

        lead = So101(a.leader, port=a.leader_port)
        fol = So101(a.follower, port=a.follower_port)
        print(fol.a_teleop(leader=a.leader, on=True, hz=a.hz), flush=True)
        try:
            t_end = _t.time() + a.seconds if a.seconds else None
            while t_end is None or _t.time() < t_end:
                _t.sleep(1.0)
                print("samples", fol.teleop["samples"], {k: round(v) for k, v in fol.q.items()}, flush=True)
        except KeyboardInterrupt:
            pass
        print(fol.a_teleop(on=False))
        fol.set_torque(False)
        lead.close(); fol.close()
        return
    if argv and argv[0] == "hub":
        hub_console(argv[1:])
        return
    if argv and argv[0] == "scan":
        found = discover(argv[1] if len(argv) > 1 else "auto")
        print_table("Serial ports", [(f["device"], f["kind"], str(f["detail"]), f.get("serial") or "") for f in found],
                    ("port", "kind", "detail", "usb serial"))
        return
    ap = argparse.ArgumentParser(prog="labrat_bridge")
    ap.add_argument("--server", default=os.environ.get("LABRAT_SERVER", "ws://localhost:8787"))
    ap.add_argument("--token", default=os.environ.get("LABRAT_TOKEN", "labrat"))
    ap.add_argument("--mock", default=None, help="'all' or comma list of device ids to mock")
    ap.add_argument("--ports", default="auto", help="'auto' or comma list of ports to probe")
    ap.add_argument("--so-a", dest="so_a")
    ap.add_argument("--so-b", dest="so_b")
    ap.add_argument("--no-fallback", action="store_true", help="report missing devices as error instead of mocking")
    a = ap.parse_args(argv)
    devices = build_devices(parse_mock(a.mock), a.ports, a.so_a, a.so_b, fallback_mock=not a.no_fallback)
    br = Bridge(devices, bridge_url(a.server, a.token))
    try:
        asyncio.run(br.run())
    except KeyboardInterrupt:
        pass
    finally:
        for d in devices.values():
            try:
                d.safe_stop()
                d.close()
            except Exception:
                pass


def hub_console(argv):
    """Send firmware lines to the servo hub and print replies + events. Pings while it runs (watchdog stays fed)."""
    import json as _json

    from .bridge import discover
    from .servo_hub import ServoHub

    ap = argparse.ArgumentParser(prog="labrat_bridge hub")
    ap.add_argument("--port", help="serial port, or 'mock' (default: autodetect by hello)")
    ap.add_argument("lines", nargs="*", help="JSON lines to send; none = read them from stdin")
    a = ap.parse_args(argv)
    port = a.port
    if port is None:
        hubs = [f["device"] for f in discover("auto") if f["kind"] == "servo_hub"]
        if not hubs:
            sys.exit("no servo hub found (try --port)")
        port = hubs[0]
    hub = ServoHub("hub", mock=True) if port == "mock" else ServoHub("hub", port=port)
    hub.event_sink = lambda _id, ev: print("event", _json.dumps(ev), flush=True)
    hub.on_link(True)
    print("hello", _json.dumps(hub.info), flush=True)

    def send(text: str):
        try:
            msg = _json.loads(text)
            print(_json.dumps(hub.a_line(msg)), flush=True)
        except Exception as e:
            print("error:", e, getattr(e, "data", ""), flush=True)

    try:
        if a.lines:
            for text in a.lines:
                send(text)
        else:
            for text in sys.stdin:
                if text.strip():
                    send(text)
    except KeyboardInterrupt:
        pass
    finally:
        hub.close()


if __name__ == "__main__":
    main()
