"""Host-owned CPU container freeze; the model gateway remains outside the freezer."""

import math
import threading
import uuid

from .freezer import ContainerFreezer


class ProviderWait:
    def __init__(self, runner):
        self.runner = runner
        self.suspended = threading.Event()
        self.ack = threading.Event()
        self.identifier = None
        self.lock = threading.RLock()
        self.freezer = ContainerFreezer(runner)
        self.needs_thaw = False

    def acknowledge(self, identifier):
        if identifier == self.identifier:
            self.ack.set()

    def command(self, action):
        self.freezer.transition(action == "pause")

    def preflight(self, engine):
        report = self.freezer.prepare(engine)
        try:
            self.signal("provider_wait_prepare")
            self.needs_thaw = True
            self.command("pause")
            self.resume()
        except Exception:
            self.runner.store.write("freezer.json", report | {"preflight": "failed"})
            raise
        self.runner.store.write("freezer.json", report | {"preflight": "passed"})
        self.runner.store.event("provider_wait_preflight", backend=self.freezer.backend)

    def signal(self, kind, wait=True):
        self.identifier = uuid.uuid4().hex
        self.ack.clear()
        self.runner.send(kind, id=self.identifier)
        if wait and not self.ack.wait(10):
            raise RuntimeError("Container pause handshake timed out")

    def pause(self):
        with self.lock:
            runner = self.runner
            if self.suspended.is_set():
                return
            if runner.gpu_allocation and runner.gpu_allocation.get("assigned") != "none":
                # Docker's CPU freezer cannot suspend kernels already queued on GPUs.
                runner.store.event(
                    "provider_wait_unsupported",
                    reason="gpu_compute_cannot_be_frozen_by_container_pause",
                )
                raise ValueError("Provider wait currently requires a CPU-only evaluation")
            # Do not freeze an accepted validator halfway through its own wall timeout.
            while not runner.evaluation_slot.acquire(timeout=0.1):
                if not runner.budget.check():
                    raise RuntimeError("Evaluation stopped while draining validation for pause")
            try:
                if not runner.budget.check():
                    return
                self.signal("provider_wait_prepare")
                self.needs_thaw = True
                try:
                    self.command("pause")
                except Exception:
                    # A timeout can leave a partial or completed freeze. Always
                    # thaw before waiting for an acknowledgement from the bridge.
                    self.resume()
                    raise
                self.suspended.set()  # Heartbeats must not fill stdin while frozen.
                runner.budget.pause()
                runner.store.event(
                    "provider_wait_paused",
                    container=runner.name,
                    computation="cpu_container_frozen",
                    freezer_backend=self.freezer.backend,
                )
                runner.store.write("status.json", runner.status())
            finally:
                runner.evaluation_slot.release()

    def resume(self, stopping=False):
        with self.lock:
            if not self.suspended.is_set() and not self.needs_thaw:
                return
            runner = self.runner
            # Queue the watchdog reset before unfreezing. At shutdown also queue
            # termination before the agent is allowed to consume any new reply.
            if stopping:
                runner.send("stop_agent")
            self.signal("provider_wait_resume", wait=False)
            # Count thaw latency: a delayed CLI acknowledgement must not permit
            # background work while the accounting clock is still stopped.
            runner.budget.resume()
            self.command("unpause")
            self.needs_thaw = False
            self.suspended.clear()
            if not self.ack.wait(10):
                raise RuntimeError("Container resume handshake timed out")
            runner.store.event(
                "provider_wait_resumed",
                stopping=stopping,
                paused_seconds=runner.budget.status()["paused_seconds"],
            )
            runner.store.write("status.json", runner.status())

    def close(self):
        self.resume(stopping=True)


def model_timeout(config):
    """Agent-side timeouts must survive a physically frozen process's wall clock."""
    if config["recovery"].get("clock_policy") != "provider_wait":
        return None
    return math.ceil(
        config["budget"]["wall_seconds"]
        + config["recovery"].get("provider_wait_seconds", 3600)
        + 120
    )
