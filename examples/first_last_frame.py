"""Видео между двумя картинками: python3 examples/first_last_frame.py start.png end.png"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from freevideo_api import FreeVideo

first, last = sys.argv[1], sys.argv[2]
fv = FreeVideo()
job = fv.video(
    "The camera slowly pushes in while the scene transforms smoothly from the first picture to the last",
    first_frame=first, last_frame=last, seconds=6, quality="medium",
)
done = fv.wait(job["id"], verbose=True)
print("saved:", fv.save(done, "first_last.mp4"))
