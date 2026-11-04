# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""Tests du préflight d'accès Git (repo_access.check_repo_access)."""
import pytest

import app.repo_access as mod
from app.repo_access import check_repo_access


@pytest.mark.asyncio
async def test_ok_when_ls_remote_succeeds(monkeypatch):
    async def fake_ls(url, token):
        return 0, ""
    monkeypatch.setattr(mod, "_ls_remote", fake_ls)
    r = await check_repo_access("https://github.com/o/r.git", "ghp_x")
    assert r.ok is True
    assert r.reason_key == "ok"


@pytest.mark.asyncio
async def test_access_denied_private_with_token(monkeypatch):
    """LS-remote KO + API 404 avec token => SSO/scope (le cas utilisateur)."""
    async def fake_ls(url, token):
        return 128, "fatal: Authentication failed"
    async def fake_api(owner_repo, token):
        return 404
    monkeypatch.setattr(mod, "_ls_remote", fake_ls)
    monkeypatch.setattr(mod, "_api_repo_status", fake_api)
    r = await check_repo_access("https://github.com/o/r.git", "ghp_x")
    assert r.ok is False
    assert r.reason_key == "access_denied"
    assert "SSO" in r.reason


@pytest.mark.asyncio
async def test_no_token_private(monkeypatch):
    async def fake_ls(url, token):
        return 128, "could not read Username"
    async def fake_api(owner_repo, token):
        return 404
    monkeypatch.setattr(mod, "_ls_remote", fake_ls)
    monkeypatch.setattr(mod, "_api_repo_status", fake_api)
    r = await check_repo_access("https://github.com/o/r.git", None)
    assert r.reason_key == "no_token_private"
    assert "token" in r.reason.lower()


@pytest.mark.asyncio
async def test_token_invalid(monkeypatch):
    async def fake_ls(url, token):
        return 128, "fatal: Authentication failed"
    async def fake_api(owner_repo, token):
        return 401
    monkeypatch.setattr(mod, "_ls_remote", fake_ls)
    monkeypatch.setattr(mod, "_api_repo_status", fake_api)
    r = await check_repo_access("https://github.com/o/r.git", "ghp_dead")
    assert r.reason_key == "token_invalid"


@pytest.mark.asyncio
async def test_api_ok_but_git_ko(monkeypatch):
    async def fake_ls(url, token):
        return 128, "fatal: unable to access"
    async def fake_api(owner_repo, token):
        return 200
    monkeypatch.setattr(mod, "_ls_remote", fake_ls)
    monkeypatch.setattr(mod, "_api_repo_status", fake_api)
    r = await check_repo_access("https://github.com/o/r.git", "ghp_x")
    assert r.reason_key == "git_auth_error"


@pytest.mark.asyncio
async def test_github_unreachable(monkeypatch):
    async def fake_ls(url, token):
        return 128, "Could not resolve host"
    async def fake_api(owner_repo, token):
        return None
    monkeypatch.setattr(mod, "_ls_remote", fake_ls)
    monkeypatch.setattr(mod, "_api_repo_status", fake_api)
    r = await check_repo_access("https://github.com/o/r.git", "ghp_x")
    assert r.reason_key == "github_unreachable"


@pytest.mark.asyncio
async def test_non_github_url_skipped():
    r = await check_repo_access("https://gitlab.example.com/o/r.git", None)
    assert r.ok is True
    assert r.reason_key == "non_github"
