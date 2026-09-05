"""Validated high-level mappings onto the ClinPGx API adapter."""

from __future__ import annotations

from typing import Any

from clinpgx_link.api.client import AsyncWorker, ClinPGxClient
from clinpgx_link.api.registry import ApiRegistry
from clinpgx_link.exceptions import InvalidInputError, UpstreamUnavailableError
from clinpgx_link.identity_contracts import numeric_identity_contract
from clinpgx_link.models import SourceResponse

_SEARCH_OPERATIONS = {
    "pathway": "GET /data/pathway",
    "gene": "GET /data/gene",
    "chemical": "GET /data/chemical",
    "disease": "GET /data/disease",
    "variant": "GET /data/variant/",
    "literature": "GET /data/literature",
    "guideline_annotation": "GET /data/guidelineAnnotation",
    "label": "GET /data/label",
    "summary_annotation": "GET /data/summaryAnnotation",
    "variant_annotation": "GET /data/variantAnnotation",
    "ontology_term": "GET /data/ontologyTerm",
    "data_annotation": "GET /data/dataAnnotation",
    "connection": "GET /data/connection",
}
_GET_OPERATIONS = {
    "pathway": "GET /data/pathway/{id}",
    "gene": "GET /data/gene/{id}",
    "chemical": "GET /data/chemical/{id}",
    "disease": "GET /data/disease/{id}",
    "variant": "GET /data/variant/{id}",
    "guideline_annotation": "GET /data/guidelineAnnotation/{id}",
    "label": "GET /data/label/{id}",
}


def _operation_for(entity_type: str, operations: dict[str, str]) -> str:
    operation = operations.get(entity_type)
    if operation is None:
        raise InvalidInputError(
            "Unsupported API entity type.",
            field="entity_type",
            hint="Choose an entity type published by server capabilities.",
        )
    return operation


class ApiService:
    """Expose stable point/search methods without bypassing the operation registry."""

    def __init__(self, client: ClinPGxClient, registry: ApiRegistry | None = None) -> None:
        self._client = client
        self.registry = registry or ApiRegistry()

    def configure_worker(self, worker: AsyncWorker) -> None:
        """Bind the host's worker lifetime policy before serving requests."""
        self._client.worker = worker

    async def call(
        self,
        operation: str,
        *,
        path_parameters: dict[str, Any] | None = None,
        query_parameters: dict[str, Any] | None = None,
        form_parameters: dict[str, Any] | None = None,
        representation: str = "json",
    ) -> SourceResponse:
        request = self.registry.bind(
            operation,
            path_parameters or {},
            query_parameters or {},
            form_parameters=form_parameters,
            representation=representation,
        )
        return await self._client.request(
            request.method,
            request.path,
            params=request.params,
            form=request.form,
            representation=request.representation,
        )

    async def search(
        self,
        entity_type: str,
        filters: dict[str, Any],
        view: str = "base",
    ) -> SourceResponse:
        if "view" in filters:
            raise InvalidInputError(
                "Projection must be supplied separately.",
                field="filters",
            )
        operation = _operation_for(entity_type, _SEARCH_OPERATIONS)
        return await self.call(operation, query_parameters={**filters, "view": view})

    async def get(
        self,
        entity_type: str,
        record_id: str,
        view: str = "max",
    ) -> SourceResponse:
        if entity_type == "vip":
            raise UpstreamUnavailableError(
                "The documented VIP API operation has no verified working request.",
                subtype="documented_broken_operation",
                hint="Use get_website_data with GET /site/vip/{id} for the verified fallback.",
            )
        numeric_contract = numeric_identity_contract(entity_type)
        operation = (
            numeric_contract.operation
            if numeric_contract is not None
            else _operation_for(entity_type, _GET_OPERATIONS)
        )
        return await self.call(
            operation,
            path_parameters={"id": record_id},
            query_parameters={"view": view},
        )


__all__ = ["ApiService"]
