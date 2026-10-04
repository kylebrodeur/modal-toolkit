# The demo recordings

Short, silent recordings of the toolkit in use. The terminal clip is real
fleet output; the dashboard clip is mocked data and says so on the page.

| Recording | Length | Size | What it shows |
| :--- | :--- | :--- | :--- |
| `modal-toolkit-terminal-20261022.gif` | 77s | 321 KB | `mtk doctor`, `mtk cost`, `mtk flow` against the live fleet |
| `modal-toolkit-dashboard-20261022.gif` | 5.8s | 1.2 MB | the fleet dashboard, against mocked stats |

Each recording is checked in next to the `.tape` (text) that produced it,
so the exact command and timing of every beat is reviewable.

## What each clip is, honestly

**The terminal recording** runs three commands against a warmed fleet:
the doctor's package report, the fleet idle-burn cost table, and the
dependency flow. Nothing is staged; the tape above is the whole session.

**The dashboard recording** is the real built dashboard client, but the
numbers are mocked, and the page carries a banner saying the data is
illustrative. It is here to show the surface, not a live fleet.

## Embedding

```markdown
![The Modal Toolkit CLI](https://github.com/kylebrodeur/modal-toolkit/raw/main/docs/demo/modal-toolkit-terminal-20261022.gif)
![The fleet dashboard (mocked data)](https://github.com/kylebrodeur/modal-toolkit/raw/main/docs/demo/modal-toolkit-dashboard-20261022.gif)
```

## The recording scripts

The scripts (what to say over each clip, matched to the tape's beat
times) live in the maintainer's private workspace repo, not here. The
beats themselves are derivable from the checked-in tapes.

## The lifecycle, in one picture

The static versions of some of what is on screen are in
[docs/images](../images/): the doctor, cost, and flow renders. The main
README carries [the fleet-at-a-glance section](../../README.md#the-fleet-at-a-glance)
for those.