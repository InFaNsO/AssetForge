"""Shared Meshy HTTP client used by every Meshy backend.

All Meshy endpoints follow the same async pattern:
  POST /<endpoint>           -> {"result": task_id}
  GET  /<endpoint>/<task_id> -> {status, model_urls, ...}

Statuses: PENDING | IN_PROGRESS | SUCCEEDED | FAILED
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Optional

BASE_URL = "https://api.meshy.ai/openapi/v1"
_FAILED = {"FAILED"}


class MeshyError(RuntimeError):
    pass


class MeshyClient:
    """Minimal injectable Meshy HTTP client (stdlib only, no extra deps)."""

    def post(self, endpoint: str, api_key: str, body: dict) -> dict:
        url = f"{BASE_URL}/{endpoint}"
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())

    def get(self, endpoint: str, api_key: str) -> dict:
        url = f"{BASE_URL}/{endpoint}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())

    def download(self, url: str, dest: str) -> str:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        urllib.request.urlretrieve(url, dest)
        return dest

    def poll(self, endpoint: str, api_key: str, task_id: str,
             poll_interval: float = 3.0, timeout_s: float = 300.0) -> dict:
        deadline = time.monotonic() + timeout_s
        while True:
            data = self.get(f"{endpoint}/{task_id}", api_key)
            status = data.get("status", "")
            if status == "SUCCEEDED":
                return data
            if status in _FAILED:
                raise MeshyError(
                    f"Meshy task {task_id} failed: "
                    f"{data.get('task_error', 'unknown error')}")
            if time.monotonic() > deadline:
                raise MeshyError(f"Meshy task {task_id} timed out (status={status})")
            time.sleep(poll_interval)
