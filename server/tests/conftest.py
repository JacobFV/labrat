import os

for k in ("ANTHROPIC_API_KEY", "FIREWORKS_API_KEY", "MONGODB_URI", "VOYAGE_API_KEY", "RAINDROP_API_KEY",
          "ELEVENLABS_API_KEY", "MODELENCE_URL"):
    os.environ[k] = ""
os.environ["LABRAT_TOKEN"] = "labrat"
os.environ["LABRAT_AUTO_APPROVE"] = "0"
