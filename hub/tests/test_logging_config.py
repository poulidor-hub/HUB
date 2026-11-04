# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 POULIDOR Audit & Développement (Valérian Poulidor)

"""
Tests logging horodaté à l'heure du Hub :
- HubClock : offset hub/local (point médian), fallback heure locale UTC
- HubTimeFormatter : format ISO 8601 UTC (…Z) sourcé de HubClock
- setup_logging : root + uvicorn reconfigurés, idempotent
- GET /api/system/time : epoch_ms (contrat de synchro des apps du parc)
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

import pytest

from app.logging_config import HubClock, HubTimeFormatter, setup_logging


@pytest.fixture(autouse=True)
def _fresh_clock():
    """Clock isolé par test + restauration de la config logging globale."""
    HubClock.reset()
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    uvicorn_names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    saved_uvicorn = {
        n: logging.getLogger(n).handlers[:]
        for n in uvicorn_names
    }
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for n, handlers in saved_uvicorn.items():
        logging.getLogger(n).handlers[:] = handlers
    HubClock.reset()


class TestHubClock:
    def test_fallback_horloge_locale(self):
        """Pas de synchro → offset nul, now_utc = heure locale UTC."""
        clock = HubClock()
        before = datetime.now(timezone.utc)
        now = clock.now_utc()
        after = datetime.now(timezone.utc)
        assert not clock.synced
        assert clock.offset_seconds == 0.0
        assert before <= now <= after

    def test_apply_epoch_ms_point_median(self):
        """offset = epoch_hub − epoch_local (latence absorbée par le point médian)."""
        clock = HubClock()
        local = time.time()
        hub_ms = int((local + 120.5) * 1000)  # Hub « en avance » de 2 min 05
        offset = clock.apply_epoch_ms(hub_ms, local - 0.05, local + 0.05)
        assert clock.synced
        assert 120.0 <= offset <= 121.0
        drift = (clock.now_utc() - datetime.now(timezone.utc)).total_seconds()
        assert 120.0 <= drift <= 121.0

    def test_singleton_partage(self):
        assert HubClock.shared() is HubClock.shared()

    def test_reset(self):
        HubClock.shared()
        HubClock.reset()
        assert HubClock._shared is None


class TestHubTimeFormatter:
    @staticmethod
    def _record() -> logging.LogRecord:
        return logging.LogRecord("app.x", logging.INFO, "f.py", 1, "msg", None, None)

    def test_format_iso8601_utc(self):
        line = HubTimeFormatter("%(asctime)s | %(message)s").format(self._record())
        ts, msg = line.split(" | ", 1)
        assert msg == "msg"
        # Ne lève pas si le format ISO UTC est respecté
        parsed = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")
        assert parsed.tzinfo is None  # suffixe Z, valeur UTC

    def test_format_suit_l_offset_hub(self):
        """L'horodatage du log reflète l'heure du Hub (offset appliqué)."""
        HubClock.shared().apply_epoch_ms(
            int((time.time() + 3600) * 1000), time.time() - 0.01, time.time() + 0.01
        )
        line = HubTimeFormatter("%(asctime)s").format(self._record())
        ts = datetime.strptime(line, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        drift = abs((ts - datetime.now(timezone.utc)).total_seconds() - 3600)
        assert drift < 5


class TestSetupLogging:
    def test_root_configure_idempotent(self):
        setup_logging("WARNING")
        setup_logging("WARNING")
        root = logging.getLogger()
        hub_handlers = [h for h in root.handlers if isinstance(h.formatter, HubTimeFormatter)]
        assert len(hub_handlers) == 1
        assert root.level == logging.WARNING

    def test_level_par_defaut_info_env(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        setup_logging()
        assert logging.getLogger().level == logging.DEBUG

    def test_level_invalide_retombe_sur_info(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "CERTAINEMENT_PAS_UN_NIVEAU")
        setup_logging()
        assert logging.getLogger().level == logging.INFO

    def test_loggers_uvicorn_reconfigures(self):
        setup_logging()
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            assert any(isinstance(h.formatter, HubTimeFormatter) for h in lg.handlers)
            assert lg.propagate is False

    def test_log_applicatif_contient_timestamp_hub(self, caplog):
        setup_logging()
        rec = logging.LogRecord("app.x", logging.INFO, "f.py", 1, "m", None, None)
        handler = next(
            h for h in logging.getLogger().handlers
            if isinstance(h.formatter, HubTimeFormatter)
        )
        line = handler.format(rec)
        ts = line.split(" | ")[0]
        assert "app.x" in line and "INFO" in line
        assert "T" in ts and ts.endswith("Z")


class TestSystemTimeEndpoint:
    def test_epoch_ms_present_et_coherent(self):
        """Contrat de synchro : epoch_ms (ms) proche de l'horloge du Hub."""
        from app.main import get_system_time

        data = asyncio.run(get_system_time())
        assert data["timezone"] == "UTC"
        assert "utc_now" in data and "utc_now_human" in data  # rétro-compat
        epoch_ms = data["epoch_ms"]
        assert isinstance(epoch_ms, int)
        assert abs(epoch_ms / 1000.0 - time.time()) < 5
