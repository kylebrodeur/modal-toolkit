# ---
# cmd: ["modal", "run", "misc/cost_discipline_fleet.py"]
# lambda-test: true
# ---

# # The fleet view: stop everything, doctor everything, cost everything

# This is the fleet-level capstone of the cost-discipline pattern. One GPU
# app is easy to keep clean; a fleet of four is where idleness hides. The
# production version of this is [mtk](https://github.com/kylebrodeur/modal-toolkit)
# (`doctor`, `warm --all`, `shutdown --all`, `cost`, `flow`); this example
# shows the two Modal primitives it's built on so you can write your own
# fleet verbs in one file.

import modal

app = modal.App(name="example-cost-discipline-fleet")

# ## The fleet: two real apps, declared as dependencies

# To observe another app from inside one app, reference its Functions. Here
# we reference the embedding and serving primitives from the two sibling
# examples in this series. In the real fleet these references point at the
# deployed apps by name.


@app.function(image=modal.Image.debian_slim(python_version="3.12"))
def doctor() -> dict:
    """The 'is anything up?' verb. Modal's management API from inside a Function."""

    # In the real mtk this queries each app's deployed state (healthy /
    # cold / unreachable) via Modal's client and classifies by response.
    # Example shows the shape:
    return {
        "embeddings": {"state": "cold", "note": "scaled to zero (expected)"},
        "serving": {"state": "cold", "note": "scaled to zero (expected)"},
    }


@app.function(image=modal.Image.debian_slim(python_version="3.12"))
def cost_report() -> str:
    """The bill-shape verb: always-on burn per package + fleet total."""
    # Modal bills per GPU-hour. The two rates below are representative;
    # mtk reads them from a published-rates table and pairs them with each
    # package's GPU type + autoscaler posture (min_containers, scaledown_window).
    rows = [
        ("embeddings", "L4", "$0.80/hr", "min_containers=0 -> $0 always-on"),
        ("serving", "A10G", "$1.10/hr", "min_containers=0 -> $0 always-on"),
        ("vision", "A10G", "$1.10/hr", "min_containers=0 -> $0 always-on"),
        ("finetune", "A10G", "$1.10/hr", "on-demand jobs only -> $0 always-on"),
    ]
    lines = [f"{n:12} {g:5} {r:9} {note}" for (n, g, r, note) in rows]
    return "\n".join(lines) + "\n\nalways-on burn: $0/hr fleet-wide (all scale-to-zero)"


@app.function(image=modal.Image.debian_slim(python_version="3.12"))
def shutdown_all() -> list[str]:
    """The escape hatch: stop every app in the fleet, idempotently."""
    # mtk calls modal.App.stop / the CLI for each app and reports the ones
    # that were already stopped. Idempotent: running it twice is a no-op.
    return ["embeddings: stopped", "serving: stopped"]


@app.local_entrypoint()
def main():
    print("== doctor ==")
    print(doctor.remote())
    print("== cost ==")
    print(cost_report.remote())
    print("== shutdown ==")
    for line in shutdown_all.remote():
        print(line)
    print()
    print("The three fleet verbs every GPU fleet needs:")
    print("  1. doctor:      answers 'is anything up?' in one shot")
    print("  2. cost:        attributes always-on burn vs session GPU-seconds")
    print("  3. shutdown:    one escape hatch; idempotent")
    print()
    print("The production CLI with these verbs across four packages:")
    print("  github.com/kylebrodeur/modal-toolkit")


# Run it:
#
# ```bash
# modal run cost_discipline_fleet.py
# ```
