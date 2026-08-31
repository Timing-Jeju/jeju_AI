"""수집 상태 전이와 publication 원자성 규칙."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class AcquisitionStatus(StrEnum):
    INCOMPLETE = "INCOMPLETE"
    ACQUIRED = "ACQUIRED"
    PARSE_FAILED = "PARSE_FAILED"
    QUALITY_FAILED = "QUALITY_FAILED"
    VALIDATED = "VALIDATED"
    STAGED = "STAGED"
    NO_CHANGE = "NO_CHANGE"
    PUBLISHED = "PUBLISHED"
    PUBLICATION_FAILED = "PUBLICATION_FAILED"


@dataclass(frozen=True)
class AcquisitionRecord:
    acquisition_id: str
    source_id: str
    status: AcquisitionStatus
    raw_checksum: str
    normalized_checksum: str | None
    normalization_schema_version: str


class PublicationTransaction(Protocol):
    def lock_acquisition(self, acquisition_id: str) -> AcquisitionRecord: ...

    def advisory_lock_source(self, source_id: str) -> None: ...

    def insert_publication(self, acquisition: AcquisitionRecord, dataset_version: str) -> str: ...

    def copy_projection(self, publication_id: str) -> None: ...

    def activate(self, source_id: str, publication_id: str) -> None: ...

    def mark_published(self, acquisition_id: str) -> None: ...


class PublicationUnitOfWork(Protocol):
    def transaction(self) -> AbstractContextManager[PublicationTransaction]: ...


class DatasetLifecycleService:
    def __init__(self, unit_of_work: PublicationUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    def publish(self, acquisition_id: str, source_date: str) -> str:
        with self._unit_of_work.transaction() as transaction:
            acquisition = transaction.lock_acquisition(acquisition_id)
            transaction.advisory_lock_source(acquisition.source_id)
            if acquisition.status != AcquisitionStatus.VALIDATED:
                raise ValueError("ACQUISITION_NOT_VALIDATED")
            if acquisition.normalized_checksum is None:
                raise ValueError("NORMALIZED_CHECKSUM_MISSING")
            dataset_version = f"{source_date}-{acquisition.normalized_checksum[:12]}"
            publication_id = transaction.insert_publication(acquisition, dataset_version)
            transaction.copy_projection(publication_id)
            transaction.activate(acquisition.source_id, publication_id)
            transaction.mark_published(acquisition_id)
            return publication_id
