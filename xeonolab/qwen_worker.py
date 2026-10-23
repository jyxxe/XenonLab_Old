"""One isolated CPU Qwen process, one request, no waiting queue or model credentials."""

import logging
import multiprocessing
import os
import threading
import time

from .common import APIError
from .qwen_encoder import MODEL_ID, QwenConfig, QwenEncoder


def _serve(connection, config, encoder_factory):
    # Spawn inherits environment; strip service identities before optional model imports.
    os.environ.clear()
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "OMP_NUM_THREADS": str(config.threads),
            "MKL_NUM_THREADS": str(config.threads),
            "OPENBLAS_NUM_THREADS": str(config.threads),
        }
    )
    try:
        encoder = encoder_factory(config)
        connection.send({"status": "ready"})
    except Exception:
        connection.send({"status": "unavailable"})
        connection.close()
        return
    try:
        while True:
            job = connection.recv()
            if job is None:
                return
            try:
                vectors, tokens = encoder.encode(**job)
                connection.send({"vectors": vectors, "tokens": tokens})
            except APIError as error:
                # Only controlled input failures cross IPC; never include model exception details.
                if error.status == 422:
                    connection.send({"error": error.code, "status": 422})
                else:
                    connection.send({"error": "MODEL_UNAVAILABLE", "status": 503})
            except Exception:
                connection.send({"error": "MODEL_UNAVAILABLE", "status": 503})
    except (EOFError, OSError):
        pass
    finally:
        connection.close()


class QwenWorker:
    def __init__(self, config=None, *, encoder_factory=QwenEncoder, context=None, invalid=False):
        self.config = config
        self.encoder_factory = encoder_factory
        self.context = context or multiprocessing.get_context("spawn")
        self.lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._status = "unavailable" if invalid else "not_configured"
        self.process = None
        self.connection = None
        self.started = None

    @classmethod
    def environment(cls):
        root = os.getenv("QWEN_MODEL_PATH")
        if not root:
            return cls()
        try:
            config = QwenConfig(
                root=root,
                model_revision=os.environ["QWEN_MODEL_REVISION"],
                manifest=os.getenv("QWEN_MANIFEST_FILE", os.path.join(root, ".xeonolab-qwen-manifest.json")),
                dimensions=int(os.getenv("QWEN_DIMENSIONS", "2048")),
                max_tokens=int(os.getenv("QWEN_MAX_TOKENS", "2048")),
                process_pixels=int(os.getenv("QWEN_PROCESS_PIXELS", "262144")),
                threads=int(os.getenv("QWEN_CPU_THREADS", "2")),
                timeout=float(os.getenv("QWEN_TIMEOUT_SECONDS", "120")),
                startup_timeout=float(os.getenv("QWEN_STARTUP_TIMEOUT_SECONDS", "600")),
            )
        except (KeyError, TypeError, ValueError):
            return cls(invalid=True)
        return cls(config)

    def start(self):
        with self._state_lock:
            if self.config is None or self.process is not None:
                return
            parent, child = self.context.Pipe()
            self.connection = parent
            self.process = self.context.Process(
                target=_serve, args=(child, self.config, self.encoder_factory), daemon=True
            )
            self.started = time.monotonic()
            self._status = "loading"
            try:
                self.process.start()
                child.close()
            except (OSError, ValueError, RuntimeError):
                parent.close()
                child.close()
                self.process = None
                self._status = "unavailable"

    def _stop(self):
        if self.process is not None and self.process.pid is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=1)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=1)
        if self.connection is not None:
            self.connection.close()
        self._status = "unavailable" if self.config else "not_configured"

    def close(self):
        with self._state_lock:
            self._stop()

    @property
    def status(self):
        with self._state_lock:
            if self._status == "loading":
                try:
                    if self.connection.poll():
                        self._status = self.connection.recv().get("status", "unavailable")
                        logging.getLogger("xeonolab.qwen").info("qwen.worker.%s", self._status)
                    elif time.monotonic() - self.started > self.config.startup_timeout:
                        self._stop()
                except (EOFError, OSError):
                    self._stop()
            if self._status == "ready" and not self.process.is_alive():
                self._stop()
            return self._status

    def descriptor(self):
        state = self.status
        if self.config:
            return self.config.descriptor(state)
        return {
            "id": MODEL_ID,
            "alias": "quality",
            "status": state,
            "model_revision": None,
            "dimensions": None,
            "preprocessing_revision": None,
            "normalized": True,
            "max_batch_size": None,
            "max_tokens": None,
            "image_max_bytes": None,
            "image_max_pixels": None,
        }

    def encode(self, texts, purpose="document", truncation="reject", image=None, expected=None):
        descriptor = self.descriptor()
        if descriptor["status"] != "ready":
            code = "MODEL_NOT_CONFIGURED" if descriptor["status"] == "not_configured" else "MODEL_UNAVAILABLE"
            raise APIError(503, code, "Qwen worker is not configured or ready")
        if (
            purpose not in ("query", "document")
            or truncation not in ("reject", "truncate")
            or not isinstance(texts, list)
            or not 1 <= len(texts) <= self.config.max_batch_size
            or any(
                not isinstance(t, str) or len(t) > 32768 or (image is None and not t.strip()) for t in texts
            )
        ):
            raise APIError(422, "INVALID_INPUT", "invalid Qwen encoding input")
        for key, value in (expected or {}).items():
            if value is not None and descriptor.get(key) != value:
                raise APIError(422, "MODEL_SPACE_MISMATCH", "requested Qwen model space is unavailable")
        if not self.lock.acquire(blocking=False):
            raise APIError(429, "EMBEDDING_BUSY", "Qwen worker busy; retry later")
        try:
            self.connection.send(
                {"texts": texts, "purpose": purpose, "truncation": truncation, "image": image}
            )
            if not self.connection.poll(self.config.timeout):
                with self._state_lock:
                    self._stop()
                raise APIError(503, "MODEL_TIMEOUT", "Qwen worker exceeded its request deadline")
            reply = self.connection.recv()
            if "error" in reply:
                if reply["status"] != 422:
                    with self._state_lock:
                        self._stop()
                raise APIError(reply["status"], reply["error"], "Qwen encoding failed")
            return reply["vectors"], reply["tokens"]
        except (EOFError, OSError, ValueError):
            with self._state_lock:
                self._stop()
            raise APIError(503, "MODEL_UNAVAILABLE", "Qwen worker stopped") from None
        finally:
            self.lock.release()
