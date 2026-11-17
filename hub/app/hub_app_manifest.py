# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Certification des apps GitHub : manifeste `hub-app.yaml`.

Un dépôt GitHub référencé dans le cache (`github_repo_cache`) peut déclarer
un manifeste `hub-app.yaml` à sa racine (branche par défaut). La validation
distingue trois états, matérialisés par `ManifestCheckResult.valid` :

- ``True``  : certifié (schéma OK, repository cross-checké le cas échéant) ;
- ``False`` : invalidé (``reason_key`` précise la cause) ;
- ``None``  : inconnu (jamais vérifié, ou GitHub raw injoignable) —
  inconnu ≠ non certifié : une panne réseau ne doit jamais décertifier.

Raison keys (``manifest_reason`` en base) : ``manifest_missing``,
``manifest_invalid_yaml``, ``manifest_schema_invalid``,
``manifest_repo_mismatch``, ``manifest_unverifiable``.
"""
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.github_client import fetch_github_raw_with_status, normalize_github_url
from app.models import GithubRepoCache, utcnow

MANIFEST_FILENAME = "hub-app.yaml"
HUB_APP_API_VERSION = "hub.poulidor/v1"

REASON_MISSING = "manifest_missing"
REASON_INVALID_YAML = "manifest_invalid_yaml"
REASON_SCHEMA_INVALID = "manifest_schema_invalid"
REASON_REPO_MISMATCH = "manifest_repo_mismatch"
REASON_UNVERIFIABLE = "manifest_unverifiable"


class ManifestCheckResult(BaseModel):
    """Résultat d'une vérification de manifeste (valid=None = inconnu)."""

    valid: Optional[bool] = None
    reason_key: Optional[str] = None
    name: Optional[str] = None
    slug: Optional[str] = None


class HubAppMetadata(BaseModel):
    """Bloc `metadata` du manifeste (champs inconnus tolérés)."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = Field(default=None, max_length=300)
    repository: Optional[str] = None


class HubAppSpec(BaseModel):
    """Bloc `spec` du manifeste (champs inconnus tolérés)."""

    model_config = ConfigDict(extra="allow")

    type: Literal["business", "infra", "tool"]


class HubAppManifest(BaseModel):
    """Manifeste `hub-app.yaml` (apiVersion/kind stricts, extras autorisés)."""

    model_config = ConfigDict(extra="allow")

    apiVersion: str
    kind: str
    metadata: HubAppMetadata
    spec: HubAppSpec

    @field_validator("apiVersion")
    @classmethod
    def _api_version_is_supported(cls, v: str) -> str:
        if v != HUB_APP_API_VERSION:
            raise ValueError(f"apiVersion supporté : {HUB_APP_API_VERSION}")
        return v

    @field_validator("kind")
    @classmethod
    def _kind_is_hub_app(cls, v: str) -> str:
        if v != "HubApp":
            raise ValueError("kind doit valoir HubApp")
        return v


def _canonical_repo_url(url: Optional[str]) -> Optional[str]:
    """Forme canonique d'une URL de dépôt pour le cross-check `repository`.

    Part d'un `normalize_github_url()` (convertit scp-like et ssh:// en
    HTTPS), puis retire le suffixe `.git` et le slash final : les variantes
    `git@github.com:o/r.git`, `https://github.com/o/r` et
    `https://github.com/o/r/` désignent le même dépôt et doivent matcher.
    """
    normalized = normalize_github_url(url)
    if not normalized:
        return None
    canonical = normalized.strip().rstrip("/")
    if canonical.endswith(".git"):
        canonical = canonical[: -len(".git")]
    return canonical.lower()


def validate_manifest_text(
    text: str, repo_clone_url: Optional[str] = None
) -> ManifestCheckResult:
    """Valide le texte d'un manifeste `hub-app.yaml` (pur, sans I/O).

    Enchaînement : `yaml.safe_load` → garde non-mapping → schéma Pydantic
    (`HubAppManifest`, extras tolérés) → cross-check `repository` après
    `normalize_github_url()` des deux côtés. Toute `ValidationError` est une
    invalidation de schéma ; un YAML qui ne charge pas en mapping est un
    YAML invalide (≠ schéma invalide).
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return ManifestCheckResult(valid=False, reason_key=REASON_INVALID_YAML)
    if not isinstance(data, dict):
        return ManifestCheckResult(valid=False, reason_key=REASON_INVALID_YAML)
    try:
        manifest = HubAppManifest.model_validate(data)
    except ValidationError:
        return ManifestCheckResult(valid=False, reason_key=REASON_SCHEMA_INVALID)

    name = manifest.metadata.name
    slug = manifest.metadata.slug
    if manifest.metadata.repository and repo_clone_url:
        if _canonical_repo_url(manifest.metadata.repository) != _canonical_repo_url(
            repo_clone_url
        ):
            return ManifestCheckResult(
                valid=False, reason_key=REASON_REPO_MISMATCH, name=name, slug=slug
            )
    return ManifestCheckResult(valid=True, reason_key=None, name=name, slug=slug)


async def check_manifest(
    owner: str,
    repo: str,
    default_branch: str,
    db: Optional[AsyncSession] = None,
) -> ManifestCheckResult:
    """Vérifie le manifeste `hub-app.yaml` d'un dépôt (branche par défaut).

    Fetch raw GitHub (PAT via `fetch_github_raw_with_status`), puis routage :
    panne (status 0) ou code ≥ 400 autre que 404 → résultat INCONNU
    (valid=None, jamais une décertification) ; 404 → `manifest_missing` ;
    2xx → validation du texte, cross-checkée contre le clone_url canonique
    reconstruit depuis (owner, repo).
    """
    url = (
        f"https://raw.githubusercontent.com/{owner}/{repo}/"
        f"{default_branch}/{MANIFEST_FILENAME}"
    )
    text, status = await fetch_github_raw_with_status(url, db)
    if status == 0:
        # Panne réseau : pas de réponse — inconnu ≠ non certifié.
        return ManifestCheckResult(valid=None, reason_key=None)
    if status == 404:
        return ManifestCheckResult(valid=False, reason_key=REASON_MISSING)
    if 200 <= status < 300:
        return validate_manifest_text(
            text, f"https://github.com/{owner}/{repo}.git"
        )
    # 403 rate-limit, 5xx… : statut hors 2xx sans certitude sur le fichier.
    return ManifestCheckResult(valid=None, reason_key=None)


async def update_cache_manifest(
    db: AsyncSession, repo_id: int, result: ManifestCheckResult
) -> None:
    """Écrit le certificat du manifeste sur la ligne de cache du dépôt.

    `manifest_checked_at` n'avance QUE si `result.valid is not None` : un
    résultat inconnu (GitHub raw injoignable) ne doit pas faire reculer la
    fraîcheur du dernier certificat connu. Commit la session. Aucune action
    (sans lever) si le repo_id n'est pas en cache.
    """
    res = await db.execute(
        select(GithubRepoCache).where(GithubRepoCache.repo_id == repo_id)
    )
    row = res.scalar_one_or_none()
    if row is None:
        return
    row.manifest_valid = result.valid
    row.manifest_reason = result.reason_key
    row.manifest_name = result.name
    row.manifest_slug = result.slug
    if result.valid is not None:
        row.manifest_checked_at = utcnow()
    await db.commit()
