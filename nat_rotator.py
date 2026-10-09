#!/usr/bin/env python3
"""Rotate the public address of a Cisco NAT pool when Internet access is lost.

When a NAT address is saturated or blacklisted (for example during a DDoS
attack against it), outbound traffic stops. This tool checks connectivity at a
fixed interval and, when the check fails, moves the NAT pool to the next free
address of a public range over SSH (Netmiko), then logs the address it used.

Credentials are read from environment variables, never from the code:
    NAT_ROUTER_HOST, NAT_ROUTER_USER, NAT_ROUTER_PASSWORD
"""
from __future__ import annotations

import argparse
import getpass
import ipaddress
import logging
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol

log = logging.getLogger("nat_rotator")

DEFAULT_POOL = "NET"
DEFAULT_CHECK_INTERVAL = 180  # seconds between checks while the link is up
DEFAULT_RETRY_DELAY = 30  # seconds between attempts while the link is down
DEFAULT_MAX_ATTEMPTS = 5


class Connection(Protocol):
    """The subset of a Netmiko connection that this tool uses."""

    def send_command(self, command: str) -> str: ...
    def send_command_timing(self, command: str) -> str: ...
    def send_config_set(self, commands: Iterable[str]) -> str: ...
    def disconnect(self) -> None: ...


# ------------------------------------------------------------------ addresses

def usable_addresses(cidr: str) -> list[str]:
    """All host addresses of a network, e.g. 100.10.10.0/29 -> 6 addresses."""
    network = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(ip) for ip in network.hosts()]
    if not hosts:
        raise ValueError(f"{cidr} has no usable host address")
    return hosts


def save_addresses(addresses: list[str], path: Path) -> None:
    path.write_text("\n".join(addresses) + "\n", encoding="utf-8")


def log_used_address(address: str, path: Path) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {address}\n")


# -------------------------------------------------------------- IOS commands

def clear_translations_command() -> str:
    return "clear ip nat translation *"


def pool_exists(output: str, pool: str) -> bool:
    return any(line.strip().startswith(f"ip nat pool {pool} ") for line in output.splitlines())


def remove_pool_commands(pool: str) -> list[str]:
    return [f"no ip nat pool {pool}"]


def set_pool_commands(pool: str, address: str, netmask: str) -> list[str]:
    """A one-address pool: start and end are the same public address."""
    return [f"ip nat pool {pool} {address} {address} netmask {netmask}"]


# ----------------------------------------------------------------- router ops

@dataclass
class Router:
    """Opens one SSH session per operation, like the original script."""

    params: dict
    pool: str = DEFAULT_POOL
    connect: Callable[..., Connection] | None = None

    def _open(self) -> Connection:
        if self.connect is None:
            from netmiko import ConnectHandler  # imported lazily so tests and --dry-run need no router
            self.connect = ConnectHandler
        return self.connect(**self.params)

    def move_pool(self, address: str, netmask: str) -> str:
        """Clear translations, delete the pool if present, recreate it on `address`."""
        conn = self._open()
        try:
            conn.send_command_timing(clear_translations_command())
            config = conn.send_command(f"show running-config | include ip nat pool {self.pool}")
            commands = []
            if pool_exists(config, self.pool):
                commands += remove_pool_commands(self.pool)
            commands += set_pool_commands(self.pool, address, netmask)
            return conn.send_config_set(commands)
        finally:
            conn.disconnect()


class DryRunConnection:
    """Prints the commands instead of sending them."""

    def __init__(self, **_params):
        pass

    def send_command(self, command: str) -> str:
        print(f"[dry-run] {command}")
        return ""

    def send_command_timing(self, command: str) -> str:
        print(f"[dry-run] {command}")
        return ""

    def send_config_set(self, commands: Iterable[str]) -> str:
        for command in commands:
            print(f"[dry-run] (config) {command}")
        return ""

    def disconnect(self) -> None:
        pass


# ------------------------------------------------------------- connectivity

def internet_ok(target: str, count: int = 3) -> bool:
    """Ping `target`; works on Windows (-n) and Linux/macOS (-c)."""
    flag = "-n" if platform.system().lower() == "windows" else "-c"
    result = subprocess.run(["ping", flag, str(count), target],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return result.returncode == 0


# ------------------------------------------------------------------ rotation

@dataclass
class Rotator:
    router: Router
    addresses: list[str]
    netmask: str
    used_log: Path
    check: Callable[[], bool]
    sleep: Callable[[float], None] = time.sleep
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    retry_delay: float = DEFAULT_RETRY_DELAY
    index: int = 0

    def next_address(self) -> str:
        address = self.addresses[self.index]
        self.index = (self.index + 1) % len(self.addresses)
        return address

    def recover(self) -> str | None:
        """Try the next addresses until the pool is moved or attempts run out."""
        for attempt in range(1, self.max_attempts + 1):
            address = self.next_address()
            log.warning("Internet down: moving pool %s to %s (attempt %d/%d)",
                        self.router.pool, address, attempt, self.max_attempts)
            try:
                self.router.move_pool(address, self.netmask)
                log_used_address(address, self.used_log)
                return address
            except Exception as error:  # SSH timeouts, auth errors, IOS errors
                log.error("Attempt %d failed: %s", attempt, error)
                self.sleep(self.retry_delay)
        log.error("Could not move the NAT pool after %d attempts", self.max_attempts)
        return None

    def step(self) -> str | None:
        """One check. Returns the new address if a rotation happened."""
        if self.check():
            log.info("Internet reachable")
            return None
        return self.recover()


# ----------------------------------------------------------------------- CLI

def router_params(args: argparse.Namespace) -> dict:
    host = args.host or os.environ.get("NAT_ROUTER_HOST")
    user = os.environ.get("NAT_ROUTER_USER")
    password = os.environ.get("NAT_ROUTER_PASSWORD")
    if args.dry_run:
        return {"device_type": "cisco_ios", "host": host or "dry-run"}
    if not host or not user:
        raise SystemExit("Set NAT_ROUTER_HOST and NAT_ROUTER_USER (or use --host)")
    if not password:
        password = getpass.getpass(f"SSH password for {user}@{host}: ")
    return {"device_type": "cisco_ios", "host": host, "username": user, "password": password,
            "timeout": 120, "global_delay_factor": 2}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rotate a Cisco NAT pool address when Internet access fails")
    parser.add_argument("--network", required=True, help="public range in CIDR, e.g. 100.10.10.0/29")
    parser.add_argument("--target", default="8.8.8.8", help="address pinged to test Internet access")
    parser.add_argument("--host", help="router address (default: $NAT_ROUTER_HOST)")
    parser.add_argument("--pool", default=DEFAULT_POOL)
    parser.add_argument("--interval", type=float, default=DEFAULT_CHECK_INTERVAL)
    parser.add_argument("--retry-delay", type=float, default=DEFAULT_RETRY_DELAY)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--addresses-file", type=Path, default=Path("ip_pub.txt"))
    parser.add_argument("--used-log", type=Path, default=Path("ip_util.txt"))
    parser.add_argument("--once", action="store_true", help="run a single check and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="print IOS commands instead of connecting; simulate a failed check")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    network = ipaddress.ip_network(args.network, strict=False)
    addresses = usable_addresses(args.network)
    save_addresses(addresses, args.addresses_file)
    log.info("%d usable addresses in %s saved to %s", len(addresses), network, args.addresses_file)

    router = Router(router_params(args), args.pool, DryRunConnection if args.dry_run else None)
    check = (lambda: False) if args.dry_run else (lambda: internet_ok(args.target))
    rotator = Rotator(router, addresses, str(network.netmask), args.used_log, check,
                      max_attempts=args.max_attempts, retry_delay=args.retry_delay)

    while True:
        rotator.step()
        if args.once or args.dry_run:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
