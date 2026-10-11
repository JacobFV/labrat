import json, subprocess, os, textwrap
from PIL import Image, ImageDraw, ImageFont, ImageFilter

R = "/Users/vibestartup/Code/labrat"
B = f"{R}/video/build"
W, H, FPS = 1920, 1080, 30
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
REG = "/System/Library/Fonts/Supplemental/Arial.ttf"
BG, TXT, GOLD = (0xFB, 0xFA, 0xF5), (0x1A, 0x18, 0x14), (0x8A, 0x6D, 0x00)
MUSIC = os.path.expanduser("~/Downloads/1984 Mechanical Heart Synthwave, Chillwave, Vaporwave, Outrun, Electronic, Cyberpunk.mp3")
PHONE = f"{R}/video/iphone_overlay.mov"

def f(p, s): return ImageFont.truetype(p, s)
def run(cmd): subprocess.run(cmd, check=True, capture_output=True)
def dur(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p]).strip())

segs = json.load(open(f"{B}/segs.json"))
d = [dur(p) for p in segs]
# --- timeline
gap_after = [0.35, 0.35, 0.6, 0.6, 0.35, 0.35, 0.35, 0]
starts, t = [], 1.0
for i in range(8):
    starts.append(t); t += d[i] + gap_after[i]
total = starts[7] + d[7] + 2.6
bounds = [0.0] + [starts[i] - (gap_after[i - 1] / 2) for i in range(1, 8)] + [total]
frames = [round(bounds[i + 1] * FPS) - round(bounds[i] * FPS) for i in range(8)]
total = sum(frames) / FPS
print("durs", [round(x, 2) for x in d], "total", total)

# --- narration
inp, flt = [], []
for i, p in enumerate(segs):
    inp += ["-i", p]
    ms = int(starts[i] * 1000)
    flt.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={ms}|{ms}[a{i}]")
flt.append("".join(f"[a{i}]" for i in range(8)) + f"amix=inputs=8:normalize=0,apad,atrim=0:{total}[out]")
run(["ffmpeg", "-y", *inp, "-filter_complex", ";".join(flt), "-map", "[out]", f"{B}/narration.wav"])
fo = total - 3
run(["ffmpeg", "-y", "-i", MUSIC, "-i", f"{B}/narration.wav", "-filter_complex",
     f"[0:a]atrim=0:{total},asetpts=PTS-STARTPTS,aresample=44100,volume=0.5,afade=t=in:d=1.5,afade=t=out:st={fo}:d=3[m];"
     f"[1:a]asplit=2[v1][v2];[m][v1]sidechaincompress=threshold=0.03:ratio=8:attack=30:release=600[md];"
     f"[md][v2]amix=inputs=2:normalize=0,alimiter=limit=0.95,atrim=0:{total}[out]",
     "-map", "[out]", "-c:a", "pcm_s16le", f"{B}/mix.wav"])

# --- graphics helpers
def blank(): return Image.new("RGBA", (W, H), (0, 0, 0, 0))

def pill(img, text, x0=0, x1=W, y_bottom=H - 70, size=38, maxw=None):
    dr = ImageDraw.Draw(img); fn = f(BOLD, size)
    maxw = maxw or (x1 - x0 - 120)
    words, lines, cur = text.split(), [], ""
    for w_ in words:
        tst = (cur + " " + w_).strip()
        if dr.textlength(tst, font=fn) > maxw and cur: lines.append(cur); cur = w_
        else: cur = tst
    lines.append(cur)
    lh = int(size * 1.3); tw = max(dr.textlength(l, font=fn) for l in lines)
    pw, ph = tw + 70, lh * len(lines) + 40
    px = (x0 + x1) / 2 - pw / 2; py = y_bottom - ph
    layer = blank(); ld = ImageDraw.Draw(layer)
    ld.rounded_rectangle([px, py, px + pw, py + ph], radius=28, fill=(20, 18, 14, 190))
    ld.rectangle([px + 28, py + ph - 6, px + 120, py + ph - 3], fill=(0xD4, 0xA8, 0x17, 255))
    for k, l in enumerate(lines):
        ld.text((px + 35, py + 20 + k * lh), l, font=fn, fill="white")
    img.alpha_composite(layer)

def cover(path, crop=None, out_w=3840, out_h=2160):
    im = Image.open(path).convert("RGB")
    if crop: im = im.crop(crop)
    iw, ih = im.size; s = max(out_w / iw, out_h / ih)
    im = im.resize((int(iw * s + 0.5), int(ih * s + 0.5)), Image.LANCZOS)
    l = (im.width - out_w) // 2; tp = (im.height - out_h) // 2
    return im.crop((l, tp, l + out_w, tp + out_h))

def card_bg():
    return Image.new("RGB", (3840, 2160), BG)

# --- scenes: (bg image 3840x2160, overlay rgba, zoom from,to, phone mode)
scenes = []
# 0 intro
bg = cover(f"{R}/cad/cnc3018/renders/real_bench_photo.png")
ov = blank(); dr = ImageDraw.Draw(ov)
panel = blank(); pd = ImageDraw.Draw(panel)
pd.rounded_rectangle([110, 330, 1110, 700], radius=36, fill=(251, 250, 245, 235))
ov.alpha_composite(panel); dr = ImageDraw.Draw(ov)
dr.text((160, 360), "LabRat", font=f(BOLD, 170), fill=TXT)
dr.rectangle([165, 560, 330, 568], fill=GOLD)
dr.text((165, 590), "a self-improving harness for a physical lab agent", font=f(REG, 36), fill=TXT)
dr.text((165, 638), "built today", font=f(BOLD, 36), fill=GOLD)
scenes.append((bg, ov, 1.0, 1.08, None))
# 1 bench zoom toward arms/CNC
bg = cover(f"{R}/cad/cnc3018/renders/real_bench_photo.png", crop=(240, 80, 1680, 890))
ov = blank(); pill(ov, "2× SO-101 · 3018 CNC + servo pipette · iPhone eyes/ears/voice · AWS")
scenes.append((bg, ov, 1.0, 1.15, ("idle", 380, W - 250, 40)))
# 2 ask
bg = cover(f"{R}/cad/cnc3018/renders/setup_front.png")
ov = blank(); dr = ImageDraw.Draw(ov)
fn = f(BOLD, 52); q = "“make the most orange well from red and yellow”"
tw = dr.textlength(q, font=fn); bx0 = (W - tw) / 2 - 50; bx1 = (W + tw) / 2 + 50
dr.rounded_rectangle([bx0, 380, bx1, 540], radius=40, fill=(251, 250, 245, 245), outline=GOLD, width=4)
dr.polygon([(bx1 - 140, 538), (bx1 - 90, 538), (bx1 - 70, 600)], fill=(251, 250, 245, 245))
dr.text((bx0 + 50, 425), q, font=fn, fill=TXT)
dr.text((bx0 + 10, 320), "ME", font=f(BOLD, 34), fill=GOLD)
pill(ov, "Digital twin of the bench · I ask in plain English")
scenes.append((bg, ov, 1.0, 1.06, None))
# 3 AI reply
bg = cover(f"{R}/docs/images/twin_simmany.png")
ov = blank(); pill(ov, "LabRat (iPhone): 16 protocols simulated · best 10% red · approve?", x0=0, x1=1420, maxw=1280)
scenes.append((bg, ov, 1.0, 1.06, ("talk", 900, 1450, 90)))
# 4 memory / sim
bg = cover(f"{R}/docs/images/twin_simmany.png", crop=(1200, 700, 3300, 1880))
ov = blank(); pill(ov, "MongoDB Atlas memory: episodic · semantic · procedural · taxonomic — recalled with Voyage AI · sim-before-actuation + human approval", maxw=1500)
scenes.append((bg, ov, 1.0, 1.15, None))
# 5 RSI
bg = cover(f"{R}/docs/images/twin_simreal.png")
ov = blank(); dr = ImageDraw.Draw(ov)
dr.rounded_rectangle([90, 90, 900, 520], radius=36, fill=(251, 250, 245, 240), outline=GOLD, width=3)
dr.text((140, 125), "PREDICTION ERROR", font=f(BOLD, 34), fill=GOLD)
dr.text((140, 175), "39.2 → 12.2", font=f(BOLD, 120), fill=TXT)
dr.text((140, 320), "−69%", font=f(BOLD, 80), fill=GOLD)
dr.text((140, 425), "after one real observation · traced in Raindrop", font=f(REG, 30), fill=TXT)
pill(ov, "Self-improvement: refit simulator → re-score plans → promote to procedural memory")
scenes.append((bg, ov, 1.0, 1.08, None))
# 6 sponsors
im = Image.new("RGB", (W, H), BG); dr = ImageDraw.Draw(im)
hd = "Built with"; fn = f(BOLD, 80)
dr.text(((W - dr.textlength(hd, font=fn)) / 2, 150), hd, font=fn, fill=TXT)
dr.rectangle([W / 2 - 80, 270, W / 2 + 80, 277], fill=GOLD)
logos = []
for n in ["claude", "mongodb", "voyageai", "aws", "raindrop", "modelence", "elevenlabs"]:
    lg = Image.open(f"{R}/docs/images/sponsors/{n}.png").convert("RGBA")
    lg = lg.crop(lg.getbbox() or (0, 0, *lg.size))
    h = 95; w = int(lg.width * h / lg.height)
    if w > 380: w = 380; h = int(lg.height * w / lg.width)
    logos.append(lg.resize((w, h), Image.LANCZOS))
rows = [logos[:3], logos[3:5], logos[5:]]
for r_, row in enumerate(rows):
    gapx = 90; tw = sum(l.width for l in row) + gapx * (len(row) - 1); x = (W - tw) / 2
    cy = 430 + r_ * 200
    for l in row:
        im.paste(l, (int(x), int(cy - l.height / 2)), l); x += l.width + gapx
bg = im.resize((3840, 2160), Image.LANCZOS)
ov = blank(); scenes.append((bg, ov, 1.0, 1.04, None))
# 7 close
im = Image.new("RGB", (W, H), BG); dr = ImageDraw.Draw(im)
dr.text((150, 220), "LabRat", font=f(BOLD, 64), fill=GOLD)
for k, l in enumerate(["Think in simulation.", "Act with permission.", "Remember everything."]):
    dr.text((150, 340 + k * 130), l, font=f(BOLD, 100), fill=TXT)
dr.rectangle([150, 760, 330, 768], fill=GOLD)
dr.text((150, 800), "github.com/JacobFV/labrat", font=f(REG, 52), fill=TXT)
bg = im.resize((3840, 2160), Image.LANCZOS)
scenes.append((bg, blank(), 1.0, 1.03, ("idle", 820, 1380, 130)))

# --- render
parts = []
ONLY = os.environ.get('ONLY')
for i, (bgim, ov, z0, z1, ph) in enumerate(scenes):
    if ONLY and str(i) not in ONLY.split(','):
        parts.append(f'{B}/scene{i}.mp4'); continue
    n = frames[i]; dsec = n / FPS
    bgim.save(f"{B}/s{i}_bg.png"); ov.save(f"{B}/s{i}_ov.png")
    zexpr = f"{z0}+{z1 - z0}*on/{n}"
    fc = (f"[0:v]zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS},format=rgba[bg];"
          f"[bg][1:v]overlay=0:0[v0]")
    inputs = ["-loop", "1", "-framerate", str(FPS), "-t", f"{dsec}", "-i", f"{B}/s{i}_bg.png",
              "-loop", "1", "-framerate", str(FPS), "-t", f"{dsec}", "-i", f"{B}/s{i}_ov.png"]
    last = "v0"
    if ph:
        mode, hh, px, py = ph
        a, b = (7.5, 10.5) if mode == "talk" else (0.0, 2.5)
        inputs += ["-i", PHONE]
        fc += (f";[2:v]trim=start={a}:end={b},setpts=PTS-STARTPTS,fps={FPS},scale=-2:{hh},format=rgba,"
               f"loop=loop=-1:size={int((b - a) * FPS)}:start=0,trim=end_frame={n}[ph];[v0][ph]overlay={px}:{py}[v1]")
        last = "v1"
    fc += f";[{last}]format=yuv420p[out]"
    out = f"{B}/scene{i}.mp4"
    run(["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", "[out]", "-frames:v", str(n),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(FPS), out])
    parts.append(out); print("scene", i, n, "frames")

open(f"{B}/list.txt", "w").write("".join(f"file '{p}'\n" for p in parts))
final = f"{R}/video/labrat_demo_final.mp4"
run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f"{B}/list.txt", "-i", f"{B}/mix.wav",
     "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total}",
     "-movflags", "+faststart", final])
print("done", final, total)
