"""Closed public entity and view vocabularies for record-oriented tools."""

from typing import Literal

SearchEntity = Literal[
    "allele",
    "annotation_id",
    "chemical",
    "connection",
    "data_annotation",
    "disease",
    "gene",
    "guideline_annotation",
    "label",
    "literature",
    "ontology_term",
    "pathway",
    "summary_annotation",
    "variant",
    "variant_annotation",
]
DetailEntity = Literal[
    "allele",
    "annotation_id",
    "chemical",
    "disease",
    "gene",
    "guideline",
    "guideline_annotation",
    "haplotype",
    "label",
    "literature",
    "pathway",
    "summary_annotation",
    "variant",
    "variant_annotation",
    "vip",
]
ResultType = Literal[
    "allele",
    "evidence",
    "guideline_annotation",
    "label",
    "literature",
    "literature_annotation",
    "multilink_annotation",
    "pathway",
    "relationship",
    "summary_annotation",
    "variant_annotation",
    "vip",
    "vip_variant",
]
ObjectType = Literal["Gene", "Chemical", "Disease", "Variant"]
View = Literal["min", "base", "max"]
ResponseMode = Literal["minimal", "compact", "standard", "full"]

__all__ = [
    "DetailEntity",
    "ObjectType",
    "ResponseMode",
    "ResultType",
    "SearchEntity",
    "View",
]
