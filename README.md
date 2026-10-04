# vectis

Role-based orchestration for a private network: DNS, an edge/load-balancer,
and two backends, deployed and operated from one CLI.

```
vectis setup    # pick a role, configure it, run a smoke test
vectis status   # this machine's role and last smoke-test result
vectis logs     # live multi-service log tail (--team for the whole deployment)
vectis fault    # controlled failure injection (server roles only)
vectis trust-cert  # trust the certificate this machine has pulled
vectis host     # serve a static site or a monorepo service on the DNS role
vectis reset    # stop locally-owned processes and clear this machine's role
```

Bare `vectis` (no subcommand) opens a persistent interactive menu with the
same seven actions plus Quit. Role selection itself is a menu of 6 roles
plus Exit. Anything slow — brew installs, cert generation, service
restarts, network checks — shows a spinner (colorful by default, set
`VECTIS_PLAIN=1` for a plain uncolored one). Errors print a short message,
not a raw Python traceback; pass `--debug` to see the full one.

## The agent has to keep running — this is not optional

`vectis setup` doesn't just write config and exit. After it configures this
machine's role, it starts background work that only means anything while
the process is alive: a heartbeat pushed to the DNS machine every 5s, a
listener so another machine's `vectis fault` can reach this one, a
supervisor that watches and restarts anything locally-owned that goes
down, and log forwarding to the team-wide aggregator. By default `setup`
then blocks in the foreground, showing a small live status panel (role,
domain, uptime, last check), until you stop it with Ctrl+C. Pass
`--no-foreground` to skip the block — only for scripting or testing, since
none of the background work above survives the process exiting.

This matters because it wasn't always true: an earlier version of `setup`
returned immediately after configuring, which meant every one of those
background threads died the instant the command finished, and the whole
point of the supervisor/dashboard/fault-injection system silently never
ran in practice. Caught and fixed before this reached real hardware.

**Honest caveat on testing this:** I confirmed directly — via a real
subprocess, not an in-process test — that the agent genuinely stays alive
and reachable (its control listener responds to requests) for as long as
the process runs, which is the part that matters most. I could not
reliably confirm the Ctrl+C-to-clean-exit path in this sandbox: SIGINT
sent to a backgrounded child process here doesn't appear to reach Python's
signal handling at all, even for a bare 3-line script with zero
dependencies on vectis, Rich, or Typer (SIGTERM to the same process works
instantly, which is what narrowed this down to a sandbox limitation rather
than application code). The `try/except KeyboardInterrupt` pattern this
relies on is standard, well-established Python behavior, and this exact
codebase already uses it successfully elsewhere (`vectis logs`'s Ctrl+C
handling) — but I'm not going to claim a specific empirical confirmation I
don't actually have. Worth a two-minute check on real hardware before
relying on it.

## Two bugs real hardware found that this sandbox couldn't

The first real run on an actual Mac surfaced two genuine bugs no amount
of testing in a Linux sandbox would have caught:

**dnsmasq/nginx restarts needed sudo, silently.** Homebrew's own dnsmasq
install warns "`dnsmasq` must be run as root to start at system startup"
— that's not cosmetic. Port 53 (and nginx's 443) are privileged; a
non-root `brew services restart` reports "Successfully started" while the
process fails to actually bind the port. The `dig` smoke test then hangs
until timeout, because nothing is listening to answer it — not a fast
refusal, just silence. Fixed by using `sudo brew services restart` for
both dnsmasq and nginx (and `sudo brew services stop` in `reset --hard`),
with a short comment at each call site explaining why the plain version
is misleading rather than just wrong. Also added a plain (non-sudo)
`brew services stop` immediately before each sudo restart, since a
machine that ran an earlier, pre-fix version of this tool may already
have a non-root LaunchAgent registered for dnsmasq/nginx that can't bind
the port but stays loaded and retrying — restarting only the root-owned
version on top of that risks both fighting over the same port instead of
one cleanly replacing the other.

**The interactive menu crashed entering Setup.** Selecting "Setup / change
role" from the bare `vectis` menu failed with `Unknown role
'<typer.models.OptionInfo object at ...>'`. Cause: the menu loop called
`setup()` as a direct Python function call, not through Typer's CLI
parser — and a `typer.Option(...)` default only resolves into its actual
value (e.g. `None`) inside Click's own invocation machinery. A bare call
bypasses that entirely, leaving the parameter holding the raw sentinel
object. Fixed by moving the real logic into plain functions
(`_setup_impl`, `_logs_impl`) with ordinary Python defaults, with the
Typer-decorated commands now thin wrappers around them, and the menu loop
calling the plain versions directly. This is the kind of bug that only
shows up when a command's bare-Python-call path is actually exercised —
all my prior testing went through `CliRunner.invoke()`, which *does* go
through Click's real parameter resolution, so it never hit this.

**The printed cert-pull command baked in the wrong user's home
directory.** The edge role prints a `curl` command for every other
machine to run. It was built from `pulled_path(domain)`, which resolves
`Path.home()` — on the *edge* machine, at the moment the string is
constructed — into something like `/Users/alice/.config/vectis/...`. Fine
for Alice; broken for anyone else who copy-pastes that exact string,
since `/Users/alice/...` isn't a path on their own machine. Fixed by
printing a literal `$HOME` in the command instead of a pre-resolved path,
so each person's own shell expands it for themselves when they actually
run it — verified by simulating a completely different user (a fake
`$HOME`, no `VECTIS_HOME` override) running the printed string verbatim,
and confirming it lands exactly where that same user's `is_pulled()`
checks.

## Resetting a role

```
vectis reset          # stop this machine's own spawned processes, clear its role
vectis reset --hard    # also stop dnsmasq/nginx and delete generated certs + written config
```

`reset` only affects the process invoking it — if a `vectis setup` agent
is running in another terminal, Ctrl+C it there first; `reset` doesn't
reach into that process.

## Cert distribution

The edge role generates its own cert and key locally — the private key
never leaves that machine. It serves only the `.crt` over plain HTTP on
the LAN (not Bluetooth, not QUIC: QUIC is built on TLS, so using it to
bootstrap trust for *this* TLS cert is circular, and macOS Bluetooth file
transfer has no stable CLI surface worth building against for a 2KB file)
and prints the exact command for every other machine to run.

That command always writes to one fixed, predictable path —
`~/.config/vectis/certs/pulled/<domain>.crt` — never wherever `curl`
happened to be invoked from. Every other command that needs to know
whether this machine already has the cert checks that exact path, so
nothing has to be told a path manually. `vectis trust-cert` uses this: it
looks the cert up itself, says plainly if it hasn't been pulled yet (with
the exact command to fix that), and otherwise trusts it.

Generation and serving are fully automatic. Pulling it is one command
someone still has to run on each other machine. Trusting it, once pulled,
is now fully automatic. The one step that stays manual regardless of
transport: installing a cert into a *different* machine's keychain needs
that machine's own local privileges — nothing can do that remotely.

A machine that later takes over the edge role always generates its own
fresh cert+key pair, even if it already has an old *pulled* copy of
someone else's cert sitting at that same path — a pulled `.crt` has no
matching private key, so it can't be reused to terminate TLS. Moving the
edge role means everyone re-pulls and re-trusts the new one.

## Hosting on the DNS role (`vectis host`)

Available when this machine's role is DNS, standalone or combined with
edge (`dns_edge`) — picking a port that doesn't collide with dnsmasq or
nginx is the operator's job when combined.

- **Static site**: point it at a folder, served on port 8080.
- **Monorepo service**: a working directory, a start command, and a port.
  Spawned as a subprocess with `PORT` set in its environment — the
  convention most Node/Python dev servers already read — so the port
  vectis tracks for the reachability check is the port the service
  actually binds, not two independently-typed numbers that happen to
  match.

Both bind `0.0.0.0`, same rule as every other service here, so any other
team machine — Backend A, Backend B, anyone — reaches it directly over the
LAN with no proxying. Once it's confirmed reachable, vectis also adds a
`hosted.<domain>` DNS record pointing at it, re-rendering dnsmasq with
whatever edge IP is already on record so the existing `app.<domain>` entry
is never clobbered. If that re-render fails (dnsmasq unreachable, no
brew), hosting itself still succeeded — you just fall back to the raw
`http://<ip>:<port>/` URL already printed, and the command says so rather
than failing outright.

A backend reaching this is a normal HTTP call, nothing vectis-specific: a
backend's own code just requests `http://hosted.<domain>:<port>/...` (or
the raw IP before the DNS record propagates) the same way it would call
any other service on the network — every role's setup already collects
the DNS machine's IP for heartbeat purposes, so that address is already on
hand in `peers['dns']` if a domain lookup isn't available yet.

## Team-wide log aggregation (`vectis logs --team`)

Every configured machine tails its own local sources — the same follower
`vectis logs` always used — and forwards each line to the DNS machine,
tagged with a timestamp and the source machine's IP. `vectis logs --team`,
run on *any* machine, tails that one shared stream instead of just the
local one: one trailing view across the whole deployment instead of going
machine to machine, the way a hosted platform's log viewer does.

## Browsing the site by domain name, not IP

Every role configures the service *it* runs — none of that makes the
machine running `vectis setup` itself able to open `https://app.<domain>`
in a browser, because nothing points that machine's own DNS lookups at
the vectis DNS server. `setup` now closes that gap: after configuring,
it offers to resolve `*.<domain>` to the team DNS server from this
machine too, via macOS's `/etc/resolver/<domain>` mechanism — the same
approach Laravel Valet and similar tools use for local dev domains.
Unlike reconfiguring the whole system's DNS resolver, this only affects
lookups ending in `<domain>`; everything else (google.com, anything else)
is untouched. It needs `sudo` the first time (prompts interactively,
same as any tool that writes under `/etc`). `vectis reset --hard` removes
it again.

Combined with `vectis trust-cert`, this is the full path from "all four
roles are configured" to "open a browser and it just works": DNS
resolution by name (this), and a cert the browser won't warn about
(`trust-cert`). For a machine not running vectis at all — a guest laptop
on the same network who just wants to look at the site — the equivalent
manual step is pointing that machine's own DNS settings at the vectis DNS
machine's IP (System Settings > Network > DNS on macOS); vectis doesn't
automate that for machines outside the deployment itself.

## Load balancer: round robin vs. least_conn, and L7 not L4/NLB

The upstream block uses nginx's `least_conn` directive, not plain round
robin (nginx's default when no directive is given). The two mostly agree
under normal conditions; they diverge right after a fault-injected
restart, when one backend has just come back up and shouldn't immediately
receive an equal share of traffic while still warming up — `least_conn`
naturally sends it fewer requests until its connection count catches up,
where round robin would blindly hand it 50% the instant nginx notices
it's alive. Given this project exists partly to test failure and recovery,
that difference is worth having; for two backends doing uniformly cheap,
uniformly fast work it wouldn't matter much either way. Switching back to
round robin is a one-line deletion in the template.

Separately: this is Layer 7 load balancing, not Layer 4/NLB. `proxy_pass`
inside an `http` block, decrypting TLS and routing on it, is what an ALB
does. It's not doing L4/NLB-style raw TCP passthrough anywhere (that's
nginx's separate `stream` module, unused here). L7 is also the only
option that works for this deployment: TLS termination and Host-header
routing to `app.<domain>` vs `api.<domain>` both require reading the
request, which an L4 balancer never does.

## Status dashboard

Every role pushes a heartbeat to the DNS machine every 5s; DNS (or
`dns_edge`) serves the aggregate at `http://<dns-ip>:9191/status`. Plain
HTTP, unauthenticated, on purpose — an internal ops view on a private LAN,
not part of the HTTPS service being served. Hosted on DNS rather than edge
deliberately: edge is the machine you're breaking on purpose during
failure testing, so the dashboard needs to live somewhere that survives
that.

## Quick start (any machine, not just Mac)

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/vectis --help
.venv/bin/vectis setup --role-name "Backend A" --no-foreground   # skip blocking, for scripting
.venv/bin/vectis status
```

State lives in `~/.config/vectis/state.json` (override with `VECTIS_HOME`).
Logs land in `~/.config/vectis/logs/`.

## Dev-mode zsh integration

```bash
cat zshrc-snippet.sh >> ~/.zshrc   # edit the path inside it first
source ~/.zshrc
vectis setup
```

Goes away once this is a Homebrew formula — `bin/vectis-bootstrap.sh` is
the placeholder for what a `Language::Python::Virtualenv` formula does
automatically at install time.

## What's tested vs. what isn't

Built and tested in a Linux container, not on macOS.

**Run and verified in this environment, including bugs this process
actually caught and fixed:**
- State store, IP/MAC fallback detection, the status dashboard end to end
  (aggregator + push + rendered page), cert generation/publish/pull with
  an explicit check the private key is never served, the reference
  backend (spawn, `/api/status`, `X-Backend` header, PID tracking), both
  Jinja2 templates, the full CLI dispatch tree, the 7th "Exit" role
  option, `--debug` vs. the short error message, the control channel
  (listener/sender/error-propagation/unreachable-peer), the supervisor's
  restart-with-backoff, local log following (no history replay, live
  capture, graceful "waiting" state, self-healing on a late-appearing
  file), fault injection's server-role gating and local-kill path, cert
  path/detection (`pulled_path`/`is_pulled`/`trust`), both hosting modes,
  the `hosted.<domain>` DNS-record wiring (including its fallback when
  dnsmasq is unreachable), team log aggregation end to end (push, forward,
  aggregate, confirmed with simulated multi-machine lines), soft and hard
  `reset` (including that --hard now also removes the /etc/resolver entry),
  `sysdns.py`'s write/read/remove logic (via a fake writable resolver
  directory), the setup-time resolver prompt end to end, the nginx
  template's `least_conn` placement, and that the foreground agent
  genuinely keeps running (verified via a real subprocess, not just
  in-process)
- **Bugs caught by testing, not by inspection, and fixed:**
  (1) `combo_backends.py`'s smoke-test merge used identical check names
  for both backends, so a dead Backend A could read as all-green because
  Backend B's results overwrote it in the merged dict — namespaced
  (`a_...`/`b_...`) and reverified.
  (2) `hosting.py`'s monorepo mode tracked a port for the reachability
  check but never told the spawned process to bind it — fixed by setting
  `PORT` in its environment, the convention most dev servers already read.
  (3) `reset --hard`'s `brew services stop` used `check=False`, which only
  suppresses a non-zero exit code, not a missing `brew` binary entirely —
  it crashed before ever deleting certs/config when brew wasn't present;
  wrapped in `try/except FileNotFoundError`.
  (4) `hosting.host_static` bound the specific detected IP instead of
  `0.0.0.0` as its own docstring claimed, while `check_reachable` always
  checks `127.0.0.1` — the two only agreed in earlier tests by coincidence
  of always testing with `bind_ip='127.0.0.1'`. Fixed to always bind
  `0.0.0.0`, matching every other service here, and the `bind_ip`
  parameter was removed rather than left as a misleading no-op.
  (5) The critical one: `setup()` never blocked, so every background
  thread it started died the moment the command returned — fixed by the
  foreground agent described above.
- **Three bugs caught on real hardware, not in this sandbox** (see "Two
  bugs real hardware found" above, plus the cert-pull one below it): the
  dnsmasq/nginx sudo requirement for privileged ports, the interactive
  menu's `OptionInfo`-leak crash entering Setup, and the pulled-cert
  command baking in the wrong user's home directory. All three fixed and
  reverified — the first two from the person's own terminal output, the
  third via a simulated different user running the exact printed command.
- **A confirmed sandbox limitation, not a code bug:** SIGINT sent to a
  backgrounded child process in this environment doesn't appear to reach
  Python's signal handling at all — reproduced with a bare 3-line script
  that has no dependency on vectis, Rich, or Typer (SIGTERM to the same
  process works instantly, which is what narrowed this to the sandbox
  rather than application code). The foreground agent's Ctrl+C handling
  is standard `try/except KeyboardInterrupt`, the same pattern already
  used successfully by `vectis logs` elsewhere in this codebase — worth a
  two-minute check on real hardware, but there's no evidence against it.

**Written correctly but not runnable here — macOS-only, unverified until
tested on real hardware** (all marked `[VERSION-DEPENDENT]` in source):
`netinfo.py`'s `route`/`ifconfig` path, `preflight.py`'s `brew`
calls, `dns_role.py`'s `dnsmasq.conf` path + `dig` + `brew services`,
`edge_role.py`'s `nginx.conf` path + `nginx -t` + `brew services`,
`certs.py`'s `mkcert` path and `security add-trusted-cert`,
`supervisor.py`'s `dns_is_up`/`process_is_up`, `fault.py`'s DNS-poison
path (inherits `DnsRole.configure()`'s status), `sysdns.py`'s real
`/etc/resolver` + `sudo` path (only the write-and-fallback logic itself
is tested, against a fake writable directory), and the control channel's
listener/sender mechanics across two *actual* physical machines on a
real LAN (the mechanics themselves are tested; a real multi-machine run,
with real firewalls and local-network permission prompts, is not).

Run the full flow on actual hardware before depending on any of the
above — these are the parts most likely to need a small fix on first
contact with real `dnsmasq`/`nginx`/`brew`.

## Next

A Homebrew tap formula (only the dev-mode zsh function/bash bootstrap
exists today), and a real run across physical machines to catch whatever
the macOS-specific paths above get wrong on first contact.
