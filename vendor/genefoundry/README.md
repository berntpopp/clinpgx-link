# GeneFoundry release contract

`data-release-manifest.schema.json` is copied without modification from
`berntpopp/genefoundry-router`, commit
`eefbb6136e458d3c2673e71e21daddac3aa32828` (peeled `v0.14.12`), path
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
