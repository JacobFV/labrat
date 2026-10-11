import os, httpx, json
from dotenv import load_dotenv; load_dotenv("/Users/vibestartup/Code/labrat/.env")
KEY = os.environ["ELEVENLABS_API_KEY"]
MALE = "nPczCjzI2devNBz1zQrb"     # Brian
AGENT = os.environ.get("ELEVENLABS_VOICE_ID") or "21m00Tcm4TlvDq8ikWAM"
if AGENT.startswith("#"): AGENT = "21m00Tcm4TlvDq8ikWAM"
SEG = [
 ("me", "I'm Jacob, and today I built LabRat: a self-improving harness for a physical lab agent."),
 ("me", "Two SO-101 arms, a CNC with a servo pipette I designed this morning, and an iPhone as its eyes, ears and voice, all driven by one agent running on AWS."),
 ("me", "I ask it: make the most orange well from red and yellow."),
 ("ai", "On it. I recalled my past runs and simulated sixteen protocols. The best one uses ten percent red. Want me to run it?"),
 ("me", "Claude recalls past runs from MongoDB Atlas, using episodic, semantic, procedural and taxonomic memory retrieved with Voyage AI. Then it simulates every protocol in a 3D digital twin. Nothing touches the hardware without a passing simulation and my approval."),
 ("me", "After the real run, the camera measures the result and the harness improves itself: it refits its simulator, re-scores old plans, and promotes what worked into procedural memory. In my test, one observation cut prediction error by sixty-nine percent, and every step is traced in Raindrop."),
 ("me", "ElevenLabs gives it a voice, Modelence runs mission control, and it's all open source."),
 ("me", "That's LabRat. Think in simulation, act with permission, remember everything."),
]
out = []
for i, (who, text) in enumerate(SEG):
    v = MALE if who == "me" else AGENT
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{v}?output_format=mp3_44100_128",
        headers={"xi-api-key": KEY}, json={"text": text, "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.2 if who == "me" else 0.35}}, timeout=120)
    r.raise_for_status()
    p = f"/Users/vibestartup/Code/labrat/video/build/seg{i}_{who}.mp3"; open(p, "wb").write(r.content); out.append(p)
    print(i, who, len(r.content))
json.dump(out, open("/Users/vibestartup/Code/labrat/video/build/segs.json", "w"))
