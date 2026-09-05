# GeneFoundry release contract

`data-release-manifest.schema.json` is copied without modification from
`berntpopp/genefoundry-router`, commit
`6568a0ad7d68925440aba550a2a678282ea5bb6b` (peeled `v0.8.7`), path
`genefoundry_router/data/data-release-manifest.schema.json`.
The upstream MIT license is retained alongside it.

`CONTRACT_SHA256` records the immutable commit, source path and exact schema digest.
Run `make vendor-check` for local integrity, or
`make vendor-check GENEFOUNDRY_ROUTER_DIR=../genefoundry-router` for exact checkout
revision and byte parity. CI always supplies its pinned router checkout; a missing
or different checkout fails instead of skipping parity. The baseline was rechecked
on 2026-09-05, including the actual schema digest and annotated tag's commit.

This outer fleet contract does not establish that a ClinPGx bundle has been built,
validated, installed or approved for distribution. Those release operations remain
separate implementation and acceptance requirements.
