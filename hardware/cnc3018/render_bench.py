"""Full-bench renders (3018 + syringe head, deck with tip rack / plate / reservoirs, SO-101s, phone, laptop, desk)
taken from the real three.js twin (apps/console/twin.js) in headless Chromium. The console is served as static files;
no coordinator is needed (its WebSocket just fails to connect and the twin stays at the pose set here).

Run (after cnc3018.py has copied the GLB to apps/console/models):
    uv run --python 3.12 --with playwright python hardware/cnc3018/render_bench.py [--chrome /path/to/chrome]
(`uv run --with playwright playwright install chromium` once, or pass --chrome.)
"""
import argparse, os, socket, subprocess, sys, time
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
REN = os.path.join(HERE, "renders")
# name: (viewport, camera position, target) in twin world mm (bench top at y=900, operator at -Z)
VIEWS = {
    "bench_full_assembly":       ((1800, 1200), (-700, 1650, -1500), (-40, 1000, 0)),
    "bench_cnc_syringe_closeup": ((1800, 1200), (-330, 1260, -520), (-20, 1080, 0)),
    "bench_syringe_head_detail": ((1800, 1200), (-170, 1250, -420), (10, 1165, -5)),
    "setup_front":               ((1456, 821), (0, 1250, -1350), (0, 1020, 0)),
    "setup_iso":                 ((1512, 794), (-900, 1500, -1250), (-30, 1000, 0)),
    "lab_iso":                   ((1512, 794), (-1300, 1900, -1900), (0, 1050, 0)),
}
POSE = "{x: 150, y: 60, z: -12}"
PIPETTE = "{tip: true, vol_ul: 120, pos_mm: 14, stroke_mm: 30}"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chrome", default=os.environ.get("CHROME"))
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    port = free_port()
    mock = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
                             "--directory", os.path.join(ROOT, "apps", "console")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=a.chrome or None,
                                  args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                                        "--ignore-gpu-blocklist"])
            pg = b.new_page(viewport={"width": 1800, "height": 1200})
            pg.on("console", lambda m: print("console:", m.text) if m.type == "error" else None)
            pg.goto(f"http://127.0.0.1:{port}/index.html")
            pg.wait_for_function("window.labrat?.twin?.cncNodes && window.labrat.twin.syrRig", timeout=120000)
            pg.wait_for_timeout(4000)  # SO-101 meshes, laptop texture
            pg.evaluate("""() => {
              const t = window.__t = window.labrat.twin;
              Object.assign(t.el.style, {position: 'fixed', inset: '0', width: '100vw', height: '100vh', zIndex: 99999, background: '#eef0f3'});
              document.body.style.overflow = 'hidden';
              if (window.labrat) window.labrat.handle = () => {};  // no coordinator: ignore any socket traffic
            }""")
            for name, (vp, pos, tgt) in VIEWS.items():
                if a.only and name not in a.only:
                    continue
                pg.set_viewport_size({"width": vp[0], "height": vp[1]})
                pg.evaluate(f"""() => {{
                  const t = window.__t; t.resize(); t.resetWells && t.resetWells();
                  t.setCNC({POSE}); t.setPipette({PIPETTE});
                  t.camera.position.set({pos[0]}, {pos[1]}, {pos[2]}); t.controls.target.set({tgt[0]}, {tgt[1]}, {tgt[2]}); t.controls.update();
                }}""")
                pg.wait_for_timeout(1500)
                out = os.path.join(REN, name + ".png")
                pg.screenshot(path=out)
                print("wrote", out)
            b.close()
    finally:
        mock.terminate()


if __name__ == "__main__":
    main()
