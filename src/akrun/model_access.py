"""Ephemeral caller-owned model transport, with no credentials in remote state."""

from __future__ import annotations

import os
import shlex
from typing import Any

from akernel_sdk import HttpReverseTunnel

from akrun.errors import ContractError, InfrastructureError
from akrun.proxy.model import ModelProxy
from akrun.proxy.protocols.anthropic import AnthropicProtocol


class ModelAccess:
    def __init__(
        self,
        *,
        model: str,
        upstream: str = "https://api.deepseek.com/anthropic",
        credential_env: str = "DEEPSEEK_API_KEY",
    ) -> None:
        credential = os.environ.get(credential_env, "")
        if not credential:
            raise ContractError(f"model credential environment variable {credential_env} is unset")
        self.model = model
        self.proxy = ModelProxy(
            upstream_url=upstream,
            credential=credential,
            protocol=AnthropicProtocol(),
            read_timeout_seconds=120,
        )

    def start(self) -> HttpReverseTunnel:
        self.proxy.start()
        return HttpReverseTunnel(
            target=f"http://{self.proxy.local_target}", reverse_port=8765, listen_port=8766
        )

    def preflight(self, sandbox: Any) -> None:
        tunnel = sandbox.reverse_tunnel
        if tunnel is None:
            raise InfrastructureError("model_tunnel_missing")
        health = (
            "import urllib.request,sys; "
            "r=urllib.request.urlopen(sys.argv[1]+'/healthz',timeout=5); "
            "assert r.read()==b'ok\\n'"
        )
        result = sandbox.commands.run(shlex.join(["python3", "-c", health, tunnel.url]), timeout=10)
        if result.exit_code != 0:
            raise InfrastructureError("model_tunnel_health_failed")
        request = self.proxy.preflight(self.model)
        sandbox.files.write("/run/akrun/preflight.json", request.body)
        script = (
            "import pathlib,urllib.request,sys; p=pathlib.Path('/run/akrun/preflight.json'); "
            "data=p.read_bytes();p.unlink(); "
            "r=urllib.request.urlopen(urllib.request.Request(sys.argv[1]+'/v1/messages',data=data,"
            "headers={'content-type':'application/json','anthropic-version':'2023-06-01',"
            "'x-api-key':'akrun-local-tunnel'},method='POST'),timeout=60); "
            "assert r.status==200; r.read()"
        )
        result = sandbox.commands.run(shlex.join(["python3", "-c", script, tunnel.url]), timeout=65)
        if result.exit_code != 0:
            raise InfrastructureError("model_protocol_preflight_failed")

    def close(self) -> None:
        self.proxy.stop()

    def summary(self) -> dict[str, Any]:
        return {
            "request_count": len(self.proxy.summaries),
            "requests": [item.as_safe_dict() for item in self.proxy.summaries],
        }
