"""Data-only project declarations. Loading them never imports model code."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ModelRegistration:
    id: str
    key: str
    name: str
    entrypoint: str
    layout: str


@dataclass(frozen=True, slots=True)
class AnalysisRegistration:
    id: str
    key: str
    name: str
    entrypoint: str


@dataclass(frozen=True, slots=True)
class WorkspaceDefinition:
    root: Path
    id: str
    models: tuple[ModelRegistration, ...]
    analyses: tuple[AnalysisRegistration, ...]
    default_model: str | None


@dataclass(frozen=True, slots=True)
class StudioTarget:
    root: Path
    legacy_reference: str | None


@dataclass(frozen=True, slots=True)
class ConfigEdit:
    path: Path
    original_sha256: str | None
    proposed_bytes: bytes
