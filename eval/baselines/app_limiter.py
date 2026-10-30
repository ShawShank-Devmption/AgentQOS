"""Owned nginx application-layer request limiter in front of the target, plus FIFO."""

from __future__ import annotations

import ipaddress
import logging
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.baselines.fifo import running_fifo
from harness.runtime import RuntimeConfig, RuntimePrerequisiteError, stop_process
from harness.topology import HOSTS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NginxConfig:
    """Explicit HTTP endpoint, upstream and request-rate settings for one owned process."""

    listen_ip: str
    listen_port: int
    upstream_ip: str
    upstream_port: int
    requests_per_second: int
    burst: int
    prefix: Path

    def __post_init__(self) -> None:
        try:
            ipaddress.IPv4Address(self.listen_ip)
            ipaddress.IPv4Address(self.upstream_ip)
        except ipaddress.AddressValueError as exc:
            raise ValueError("nginx endpoints must be IPv4 IP addresses") from exc
        if any(
            type(port) is not int or not 1 <= port <= 65_535
            for port in (self.listen_port, self.upstream_port)
        ):
            raise ValueError("nginx ports must be integers between 1 and 65535")
        if (self.listen_ip, self.listen_port) == (self.upstream_ip, self.upstream_port):
            raise ValueError("upstream must differ from nginx listener")
        if any(
            type(value) is not int or value <= 0 for value in (self.requests_per_second, self.burst)
        ):
            raise ValueError("nginx request rate and burst must be positive integers")
        if any(character in str(self.prefix) for character in ('"', "\\", "\n", "\r")):
            raise ValueError("nginx prefix must not contain config escape characters")


def render_config(config: NginxConfig) -> str:
    """Render standalone nginx config with per-source leaky-bucket request delay.

    Requests beyond the explicit burst are rejected with 429, an application-baseline behavior
    rather than classification uncertainty. Non-HTTP traffic remains on the FIFO switch.
    """
    prefix = config.prefix.resolve()
    return f'''worker_processes 1;
error_log "{prefix}/error.log" info;
pid "{prefix}/nginx.pid";
events {{ worker_connections 1024; }}
http {{
    access_log "{prefix}/access.log";
    client_body_temp_path "{prefix}/client_temp";
    proxy_temp_path "{prefix}/proxy_temp";
    limit_req_zone $binary_remote_addr zone=target:10m rate={config.requests_per_second}r/s;
    server {{
        listen {config.listen_ip}:{config.listen_port};
        location / {{
            limit_req zone=target burst={config.burst};
            limit_req_status 429;
            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_pass http://{config.upstream_ip}:{config.upstream_port};
        }}
    }}
}}
'''


def _wait_for_listener(host: Any, child: subprocess.Popen[Any], config: NginxConfig) -> None:
    script = (
        "import socket; s=socket.socket(); s.settimeout(.2); "
        f"s.connect(({config.listen_ip!r},{config.listen_port})); s.close()"
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimePrerequisiteError("owned nginx exited before listening; inspect error.log")
        probe = host.popen(
            ["python3", "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            if probe.wait(timeout=1) == 0:
                return
        except subprocess.TimeoutExpired:
            logger.debug("listener readiness probe timed out", exc_info=True)
            stop_process(probe)
        time.sleep(0.05)
    raise RuntimePrerequisiteError("nginx did not start listening within five seconds")


@contextmanager
def running_nginx(host: Any, config: NginxConfig) -> Iterator[subprocess.Popen[Any]]:
    """Validate nginx config then start/reap only the owned foreground namespace child."""
    config.prefix.mkdir(parents=True, exist_ok=True)
    path = config.prefix.resolve() / "nginx.conf"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(render_config(config))
    command = ["nginx", "-p", str(config.prefix.resolve()) + "/", "-c", str(path)]
    checker = host.popen(
        [*command, "-t"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        _, stderr = checker.communicate(timeout=10)
    finally:
        stop_process(checker)
    if checker.returncode:
        raise RuntimePrerequisiteError(f"nginx config validation failed: {stderr.strip()}")
    with (config.prefix / "process.log").open("x", encoding="utf-8") as log:
        child = host.popen(
            [*command, "-g", "daemon off; master_process off;"],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_listener(host, child, config)
            yield child
        finally:
            stop_process(child)


@contextmanager
def running_app_limiter(runtime: RuntimeConfig, config: NginxConfig) -> Iterator[Any]:
    """Run target-namespace nginx plus FIFO; caller runs its target on the upstream port."""
    if not config.prefix.resolve().is_relative_to(runtime.output_dir.resolve()):
        raise ValueError("nginx prefix must be inside this runtime's new output directory")
    with running_fifo(runtime) as network:
        target = next(host for host in HOSTS if host.role == "target")
        with running_nginx(network.get(target.name), config):
            yield network
