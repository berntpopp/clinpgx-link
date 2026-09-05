# Numeric identity profile evidence — 2026-09-06

The captured OpenAPI defines integer `id` for Literature, SummaryAnnotation, and
VariantDrugAnnotation. It defines top-level `resourceId` as required for Literature.

A retained live Literature response for internal id `15178564` provided a second observed
shape. Its exact 1,154-byte HTTP body has SHA-256
`97abd1e42c9fa645890cb5e945b95f16a81b3af1966160399ec5e1bcb5395621`. The `data` object
contains integer `id`, string `title` and `type`, and a `crossReferences` list, but no
top-level `resourceId`. Each observed cross-reference contains integer `id` and string
`resource`, `resourceId`, and `_url` fields.

The Literature adapter therefore recognizes two explicit variants: the captured top-level
`resourceId` shape and the observed `crossReferences` shape. Both retain integer `id` as the
internal detail identifier. Cross-reference `resourceId` values remain external identifiers;
they are not promoted to ClinPGx detail ids. Malformed or unknown cross-reference entry shapes
fall back to retained full/source access.
