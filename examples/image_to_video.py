"""Сначала картинка, потом видео, которое с неё начинается."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from freevideo_api import FreeVideo

fv = FreeVideo()
img = fv.wait(fv.image("A lighthouse on a cliff during a storm, cinematic", aspect="16:9")["id"], verbose=True)
fv.save(img, "lighthouse.png")

job = fv.video("Huge waves crash against the cliff, the lighthouse beam sweeps through the rain",
               first_frame="lighthouse.png", aspect="16:9", seconds=8)
print("saved:", fv.save(fv.wait(job["id"], verbose=True), "lighthouse.mp4"))
