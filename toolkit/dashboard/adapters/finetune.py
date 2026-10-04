"""U3: the `finetune` adapter.

The finetune package is a JOB package: train/eval/gguf verbs with no
long-running HTTP service and no `/health` endpoint (serving left the
package in v1.1.0 - that is inference's job now). The card therefore
renders the package's nature instead of probing: "job package, no
server". No remote probe is made; the probe succeeds trivially so the
card does not error.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..adapters import Probe


class FinetuneAdapter:
    key = "finetune"
    label = "Finetune"

    def health(self, cfg: Mapping[str, Any]) -> Probe:
        return Probe(ok=True, data={"status": "job-package", "note": "no server; verbs run on demand"})

    def detail(self, cfg: Mapping[str, Any]) -> Probe:
        return Probe(ok=True, data={"verbs": ["train", "eval", "gguf"]})


ADAPTER = FinetuneAdapter()
