# Dataset layout

Classes (blueprint): `clean`, `dusty`, `bird_drop`, `mixed`. A class folder may be missing from a
session; a class with no images at all gets weight 0 and is never predicted.

```
ml/data/
  kaggle_set_a/          <- one folder per photo SESSION (public set, day, camera...)
    clean/  dusty/  bird_drop/  mixed/
  own_panel_01/          <- your phone photos of the demo panel (use for --test-sessions)
    clean/  dusty/  bird_drop/  mixed/
  own_panel_02/ ...
```

Rules that keep the score honest:
- Keep your own demo-panel session(s) OUT of training: pass them as `--test-sessions`.
- Never put frames from the same burst in different sessions.
- Images are not committed to git (see .gitignore). Only this README is.
