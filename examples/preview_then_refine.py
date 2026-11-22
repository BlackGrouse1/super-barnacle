"""Три быстрых превью с разными сидами, затем доводка первого до полного разрешения."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from freevideo_api import FreeVideo

fv = FreeVideo()
prompt = "A paper boat sails down a rainy street gutter, macro shot, soft evening light"
previews = fv.video(prompt, seconds=6, aspect="16:9", preview=True, takes=3)
for i, job in enumerate(previews, 1):
    fv.save(fv.wait(job["id"], verbose=True), f"preview_{i}.mp4")

best = previews[0]["id"]          # выбери понравившееся превью
final = fv.video(refine_from=best)
print("saved:", fv.save(fv.wait(final["id"], verbose=True), "final.mp4"))
