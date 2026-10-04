"""Example: the full operator lifecycle with mtk.

This is the toolkit's "flight path": setup, doctor, warm, use, shutdown -
across all four packages, all from one CLI.
"""

# 1. Setup: write the shared config
#    $ mtk setup --repos-root /path/to/sibling-repos-dir
#    or, if you already have an ~/.config/mci/config.json:
#    $ mtk setup --from-env

# 2. Inspect the config
#    $ mtk config
#    -> which packages are `ok` and which are missing required keys

# 3. Health across the fleet
#    $ mtk doctor
#    -> one line per package: ok / unhealthy / unreachable / repo
#    -> with --json: full output, including `served` model list on the inference
#       lane, per-package base_url/token presence, and any HTTP errors.

# 4. Warm a package (cold-starts on first request)
#    $ mtk warm --pkg inference
#    $ mtk warm --all     # warms whatever has a GPU

# 5. Use the fleet
#    $ mtk embedding sync      # points you at the client-side flow
#    $ mtk embedding reindex    # shells into the embedding repo's modal run
#    $ mtk vision deploy
#    $ mtk vision warm
#    $ mtk finetune train --profile profiles/gemma4/profile.json
#    $ mtk finetune eval --adapter <hf-user>/<name>
#    $ mtk finetune gguf --adapter <hf-user>/<name> --outtype q4_k_m
#    $ mtk finetune serve --adapter <hf-user>/<name>

# 6. Cost across the fleet
#    $ mtk cost
#    -> per-package always-on $/hr/day/month, plus a blended total

# 7. The escape hatch
#    $ mtk shutdown --all
#    -> zeroes all GPU workers; embeddings continue because nothing GPU-side is up
