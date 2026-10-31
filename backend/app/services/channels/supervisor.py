"""``ChannelSupervisor``: runs the chat channels inside the engine process (M19).

One tick (about every second) for every enabled channel:

* take or renew the channel's **lease** (``lease_owner`` / ``lease_expires_at``; renewed every 15 s,
  expires after 60 s) so only one process serves a bot even if several engines run;
* while the lease is held: make sure the inbound worker runs (Telegram: a long-polling thread;
  Discord: the gateway client; WhatsApp has none, its webhook lives in the API) and deliver the
  channel's due outbox rows;
* when the lease is lost, or the channel is disabled / errored / removed: stop the worker.

Nothing here starts on its own: ``EngineService`` starts it when ``settings.channels_enabled`` is on;
tests drive ``tick`` and ``TelegramPoller.poll_once`` directly with fake transports.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.time import utcnow
from app.models.chat import ChannelStatus, ChatChannel
from app.services.channels import outbox
from app.services.channels import service as channel_service
from app.services.channels.common import Inbound, SendError
from app.services.channels.hub import ChannelHub, SecretsUnavailable
from app.services.channels.router import ChannelRouter

logger = logging.getLogger("company_os.channels")

LEASE_SECONDS = 60
RENEW_SECONDS = 15
TICK_SECONDS = 1.0
DIGEST_CHECK_SECONDS = 300
PRUNE_SECONDS = 3600
POLL_TIMEOUT = 25
MAX_POLL_BACKOFF = 60.0
RUNNING = (ChannelStatus.ACTIVE, ChannelStatus.CONNECTING)


# ====================================================================== Telegram polling


class TelegramPoller:
    """Long polling for one Telegram channel. ``poll_once`` is one ``getUpdates`` round (and handling
    of what it returned); the cursor is persisted after each update so a restart never repeats or
    skips one."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        hub: ChannelHub,
        router: ChannelRouter,
        channel_id: str,
    ) -> None:
        self.factory, self.hub, self.router, self.channel_id = factory, hub, router, channel_id

    def poll_once(self, *, timeout: int = POLL_TIMEOUT) -> int | None:
        """Number of updates handled, or None if the channel cannot be polled (not running)."""
        with self.factory() as db:
            channel = db.get(ChatChannel, self.channel_id)
            if channel is None or channel.status not in RUNNING or channel.kind != "telegram":
                return None
            try:
                secrets = self.hub.secrets_for(db, channel)
            except SecretsUnavailable:
                channel_service.mark_error(
                    db, channel, "The stored credentials cannot be read; connect the app again."
                )
                return None
            cursor = int(channel.poll_cursor) if (channel.poll_cursor or "").isdigit() else None
            adapter = self.hub.adapter("telegram")
        try:
            updates = adapter.get_updates(
                secrets["token"], cursor + 1 if cursor is not None else None, timeout=timeout
            )
        except SendError as exc:
            if exc.auth:
                with self.factory() as db:
                    channel = db.get(ChatChannel, self.channel_id)
                    if channel is not None:
                        channel_service.mark_error(db, channel, str(exc))
                return None
            raise
        handled = 0
        for update_ in updates:
            uid = update_.get("update_id")
            if not isinstance(uid, int):
                continue
            if cursor is not None and uid <= cursor:
                continue  # delivered twice by Telegram
            inbound = adapter.parse_update(self.channel_id, update_)
            if inbound is not None:
                self.router.process(inbound)  # never raises
                handled += 1
            cursor = uid
            with self.factory() as db:
                db.execute(
                    update(ChatChannel)
                    .where(ChatChannel.id == self.channel_id)
                    .values(poll_cursor=str(uid))
                )
                db.commit()
        return handled


class _Worker:
    """A running inbound worker (thread or gateway) with a ``stop``."""

    def __init__(self, stop: Callable[[], None]) -> None:
        self.stop = stop


# ====================================================================== the supervisor


class ChannelSupervisor:
    def __init__(
        self,
        factory: sessionmaker[Session],
        hub: ChannelHub,
        *,
        owner_id: str | None = None,
        clock: Callable[[], datetime] = utcnow,
        base_url_getter: Callable[[], str | None] | None = None,
        inbound_starter: Callable[[ChatChannel, dict[str, str]], Any] | None = None,
    ) -> None:
        self.factory = factory
        self.hub = hub
        self.owner_id = owner_id or f"engine-{uuid.uuid4().hex[:12]}"
        self.clock = clock
        self.base_url_getter = base_url_getter
        self.router = ChannelRouter(factory, hub, clock=clock, base_url_getter=base_url_getter)
        self._starter = inbound_starter or self._default_starter
        self._workers: dict[str, Any] = {}
        self._last_renew: dict[str, datetime] = {}
        self._last_digest: datetime | None = None
        self._last_prune: datetime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lease
    def _acquire(self, channel_id: str, now: datetime) -> bool:
        with self.factory() as db:
            result = db.execute(
                update(ChatChannel)
                .where(
                    ChatChannel.id == channel_id,
                    or_(
                        ChatChannel.lease_owner.is_(None),
                        ChatChannel.lease_owner == self.owner_id,
                        ChatChannel.lease_expires_at.is_(None),
                        ChatChannel.lease_expires_at < now,
                    ),
                )
                .values(
                    lease_owner=self.owner_id,
                    lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                )
            )
            db.commit()
            return bool(result.rowcount)

    def _release(self, channel_id: str) -> None:
        with self.factory() as db:
            db.execute(
                update(ChatChannel)
                .where(ChatChannel.id == channel_id, ChatChannel.lease_owner == self.owner_id)
                .values(lease_owner=None, lease_expires_at=None)
            )
            db.commit()

    def held(self) -> set[str]:
        return set(self._last_renew)

    # -- one pass
    def tick(self, now: datetime | None = None) -> dict[str, list[str]]:
        now = now or self.clock()
        with self.factory() as db:
            channels = list(db.scalars(select(ChatChannel)))
        eligible = {c.id: c for c in channels if c.status in RUNNING}
        report: dict[str, list[str]] = {"held": [], "started": [], "stopped": [], "drained": []}
        # let go of what is gone, disabled or in error
        for channel_id in list(self._last_renew):
            if channel_id not in eligible:
                self._stop_worker(channel_id)
                self._last_renew.pop(channel_id, None)
                self._release(channel_id)
                report["stopped"].append(channel_id)
        for channel_id, channel in eligible.items():
            last = self._last_renew.get(channel_id)
            if last is None or now - last >= timedelta(seconds=RENEW_SECONDS):
                if self._acquire(channel_id, now):
                    self._last_renew[channel_id] = now
                else:  # someone else serves it
                    if channel_id in self._last_renew:
                        self._stop_worker(channel_id)
                        self._last_renew.pop(channel_id, None)
                        report["stopped"].append(channel_id)
                    continue
            report["held"].append(channel_id)
            if channel_id not in self._workers and channel.kind in ("telegram", "discord"):
                self._start_worker(channel)
                if channel_id in self._workers:
                    report["started"].append(channel_id)
            if outbox.drain(
                self.factory, self.hub, channel_id, now, base_url_getter=self.base_url_getter
            ):
                report["drained"].append(channel_id)
        self._housekeeping(now)
        return report

    def _housekeeping(self, now: datetime) -> None:
        if self._last_digest is None or now - self._last_digest >= timedelta(
            seconds=DIGEST_CHECK_SECONDS
        ):
            self._last_digest = now
            try:
                with self.factory() as db:
                    outbox.enqueue_digests(db, now)
                    db.commit()
            except Exception:
                logger.exception("chat: digest queueing failed")
        if self._last_prune is None or now - self._last_prune >= timedelta(seconds=PRUNE_SECONDS):
            self._last_prune = now
            try:
                with self.factory() as db:
                    outbox.prune(db, now)
                    db.commit()
            except Exception:
                logger.exception("chat: pruning failed")

    # -- workers
    def _start_worker(self, channel: ChatChannel) -> None:
        try:
            with self.factory() as db:
                fresh = db.get(ChatChannel, channel.id)
                secrets = self.hub.secrets_for(db, fresh) if fresh else {}
        except SecretsUnavailable:
            with self.factory() as db:
                fresh = db.get(ChatChannel, channel.id)
                if fresh is not None:
                    channel_service.mark_error(
                        db, fresh, "The stored credentials cannot be read; connect the app again."
                    )
            return
        try:
            worker = self._starter(channel, secrets)
        except Exception:
            logger.exception("chat: could not start the %s worker", channel.kind)
            return
        if worker is not None:
            self._workers[channel.id] = worker

    def _stop_worker(self, channel_id: str) -> None:
        worker = self._workers.pop(channel_id, None)
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                logger.exception("chat: could not stop a worker cleanly")

    def _default_starter(self, channel: ChatChannel, secrets: dict[str, str]) -> _Worker | None:
        if channel.kind == "telegram":
            return self._start_telegram(channel)
        if channel.kind == "discord":
            return self._start_discord(channel, secrets)
        return None

    def _start_telegram(self, channel: ChatChannel) -> _Worker:
        poller = TelegramPoller(self.factory, self.hub, self.router, channel.id)
        stop = threading.Event()

        def run() -> None:
            backoff = 1.0
            while not stop.is_set():
                try:
                    result = poller.poll_once()
                    if result is None:
                        stop.wait(5.0)
                    backoff = 1.0
                except Exception as exc:
                    logger.warning("telegram polling failed (%s); retrying", type(exc).__name__)
                    stop.wait(backoff)
                    backoff = min(backoff * 2, MAX_POLL_BACKOFF)

        thread = threading.Thread(target=run, name=f"telegram-{channel.id[:8]}", daemon=True)
        thread.start()
        return _Worker(lambda: (stop.set(), thread.join(timeout=2)))

    def _start_discord(self, channel: ChatChannel, secrets: dict[str, str]) -> _Worker:
        channel_id = channel.id

        def on_ready() -> None:
            with self.factory() as db:
                row = db.get(ChatChannel, channel_id)
                if row is not None and row.status == ChannelStatus.CONNECTING:
                    row.status = ChannelStatus.ACTIVE
                    row.last_error = None
                    db.commit()

        def on_error(message: str, auth: bool) -> None:
            with self.factory() as db:
                row = db.get(ChatChannel, channel_id)
                if row is None:
                    return
                if auth:
                    channel_service.mark_error(db, row, message)
                else:
                    row.last_error = message[:500]
                    db.commit()

        def on_inbound(inbound: Inbound) -> None:
            self.router.process(inbound)

        gateway = self.hub.gateway_factory(
            token=secrets["token"],
            channel_id=channel_id,
            on_inbound=on_inbound,
            on_ready=on_ready,
            on_error=on_error,
        )
        gateway.start()
        return _Worker(gateway.stop)

    # -- the loop
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="chat-supervisor", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("chat supervisor tick failed")
            self._stop.wait(TICK_SECONDS)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        for channel_id in list(self._workers):
            self._stop_worker(channel_id)
        for channel_id in list(self._last_renew):
            try:
                self._release(channel_id)
            except Exception:
                logger.info("chat: could not release a lease on shutdown")
        self._last_renew.clear()
