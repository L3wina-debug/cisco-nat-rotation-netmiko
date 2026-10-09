# Cisco NAT Pool Rotation with Python and Netmiko

![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)
![Netmiko](https://img.shields.io/badge/Netmiko-SSH-informational)
![Cisco IOS](https://img.shields.io/badge/Cisco-IOS-1BA0D7?logo=cisco&logoColor=white)
![GNS3](https://img.shields.io/badge/Lab-GNS3-2E7D32)
![Tests](https://img.shields.io/badge/tests-12%20passing-brightgreen)
![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)

A Python tool that keeps a site online when its **NAT public address** stops working, for example when a **DDoS attack** saturates the router's NAT translation table or the address gets blacklisted. It checks Internet access at a fixed interval. When the check fails, it connects to the **Cisco router over SSH** with **Netmiko**, clears the NAT translations, moves the NAT pool to the next free address of a public range, and logs the address it used.

> Final-year project of my **BTS in Information Systems and Networks**, 2024, built and tested in a **GNS3** lab with Cisco IOS routers. The original script is kept in [`original/`](original/nat_rotation_bts_2024.py); `nat_rotator.py` is the reviewed version.

## How it works

```text
            every 180 s
 ┌──────────── ping target ───────────┐
 │                                     │
 ▼ reachable                           ▼ unreachable
 wait                     ┌─ SSH to R1 (Netmiko) ────────────────────────┐
                          │ clear ip nat translation *                  │
                          │ no ip nat pool NET          (if it exists)   │
                          │ ip nat pool NET <next IP> <next IP> netmask  │
                          └─ log address to ip_util.txt ─────────────────┘
                             SSH error → retry (30 s, up to 5 attempts)
```

The public range (e.g. `100.10.10.0/29`) is split into usable host addresses with `ipaddress`. Addresses are used in order and wrap around at the end of the range.

## Tech stack

Python · Netmiko (SSH to Cisco IOS) · `ipaddress` · `subprocess` (ping) · `logging` · GNS3 · Cisco IOS NAT (`ip nat pool`, `ip nat inside source ... overload`)

## Installation

```bash
git clone https://github.com/L3wina-debug/cisco-nat-rotation-netmiko.git
cd cisco-nat-rotation-netmiko
pip install -r requirements.txt
```

Configure the router for SSH and NAT. A complete lab example is in [`configs/R1_lab_example.cfg`](configs/R1_lab_example.cfg).

## Usage

Try it without a router. Dry-run prints the IOS commands it would send:

```bash
python nat_rotator.py --network 100.10.10.0/29 --dry-run
```

Output ([`docs/dry_run_example.txt`](docs/dry_run_example.txt)):

```text
INFO 6 usable addresses in 100.10.10.0/29 saved to ip_pub.txt
WARNING Internet down: moving pool NET to 100.10.10.1 (attempt 1/5)
[dry-run] clear ip nat translation *
[dry-run] show running-config | include ip nat pool NET
[dry-run] (config) ip nat pool NET 100.10.10.1 100.10.10.1 netmask 255.255.255.248
```

Run against the lab router (credentials come from environment variables, never from the code):

```bash
export NAT_ROUTER_HOST=192.168.174.1
export NAT_ROUTER_USER=admin
export NAT_ROUTER_PASSWORD='...'        # or leave unset to be prompted
python nat_rotator.py --network 100.10.10.0/29 --target 8.8.8.8
```

Options: `--pool NET`, `--interval 180`, `--retry-delay 30`, `--max-attempts 5`, `--once` (single check), `--used-log ip_util.txt`.

## Tests

```bash
python -m unittest discover -s tests -v
```

12 tests use a fake Netmiko connection. They cover address splitting, the IOS commands, replacing an existing pool, rotation order and wrap-around, retry after an SSH failure, and giving up after the maximum number of attempts.

## Fixes compared with the original BTS script

| Original | Fixed |
|---|---|
| `claire traduction ip nat *` is not an IOS command | `clear ip nat translation *` |
| Checked pool `NET` but deleted pool `nat` | Same pool name everywhere (`--pool`) |
| `change_nat_address()` took 2 arguments but was called with 3 | One function, `move_pool(address, netmask)` |
| Netmask fixed to `255.255.255.0` | Netmask taken from the public range |
| Router password written in the code | Environment variables or a password prompt |
| `ping -c` only works on Linux | `-n` on Windows, `-c` elsewhere |
| `print` messages | `logging` with timestamps, and a timestamped address log |

## Limits

- Rotating the public address is a **mitigation**, not a DDoS defense: an attacker who targets the new address, or the whole range, still causes an outage. Upstream filtering (ISP, scrubbing center) and rate limiting remain necessary.
- Changing the NAT address drops existing translations, so active sessions are interrupted.
- Tested in a GNS3 lab, not on a production router.

## What I learned

- **Network automation:** driving a Cisco router from Python over SSH with Netmiko instead of typing commands by hand.
- **NAT on Cisco IOS:** pools, inside/outside interfaces, translation tables, and why a saturated table cuts outbound traffic.
- **Thinking about availability:** detecting an outage, reacting automatically, retrying safely and keeping a trace of every change.
- **Reviewing my own code:** going back to my BTS script, finding the bugs and hardcoded secrets, and writing tests that would have caught them.

## Author

**Mahmoud El Aaouadi**, Master's student in AI & Cybersecurity. [Portfolio](https://mahmoud-elaaouadi.netlify.app) · [LinkedIn](https://www.linkedin.com/in/mahmoud-elaaouadi-7b3087416)

## License

[MIT](LICENSE)
