"""Which mix of red and yellow is the most orange? Nine beakers, 200 µL each, from mostly yellow to mostly red.

    cd apps/cli && uv run python ../../experiments/orange_sweep.py
"""
from pathlib import Path

from labrat_cli import Lab

lab = Lab()  # LABRAT_SERVER / LABRAT_TOKEN

samples = [{"red": r, "yellow": 200 - r} for r in range(20, 200, 20)]
e = lab.new("Orange sweep", target="orange", samples=samples)
print(f"{e['id']}: dry run {e['status']}, {e['check']['duration_s']:.0f} s, {len(e['check']['tips_used'])} tips")
for problem in e["check"]["errors"]:
    print("  error:", problem)
if e["status"] != "ready":
    raise SystemExit("fix the samples and try again")

print("running (approved by this script) ...")
e = lab.run(e["id"], approve=True, wait=True)  # blocks until done / failed / aborted
print(f"{e['id']}: {e['status']}", e["error"] or "")
print("\nwell  red yellow  predicted        measured         to orange")
for b in e["beakers"]:
    best = "  <- closest" if b["well"] == e.get("closest_measured") else ""
    print(f"{b['well']:<4} {b['red']:>4} {b['yellow']:>6}  {str(b['predicted_rgb']):<16} {str(b['measured_rgb'] or '-'):<16}"
          f" {b.get('measured_to_target') or '-'}{best}")

if not any(b["measured_rgb"] for b in e["beakers"]):
    print("no measured colours (no phone photo?): enter readings with lab.results(id, [{well, rgb}]) or in the console")

out = Path(__file__).resolve().with_name(f"orange_sweep_{e['id']}.csv")
out.write_text(lab.csv(e["id"]))
print(f"\nsaved {out}")
