# nixflake-update-checker

Inspect a local Nix flake (for example `/etc/nixos`), fetch the latest upstream
inputs in a throwaway copy, and summarize **which software would be updated and
which versions change** — plus **how much the update would download**. It does
not touch the original directory, does not build anything, and needs no `sudo`
in the common case.

```
$ nixflake-check /etc/nixos
╭─ Nix flake update check ─────────────────────────────────────────╮
│ /etc/nixos · host nixos                                          │
╰──────────────────────────────────────────────────────────────────╯
                    Flake inputs
┏━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ Input   ┃ Repository    ┃ Old rev  ┃ New rev  ┃ Commits ┃ Status         ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ nixpkgs │ NixOS/nixpkgs │ fe132422 │ 09063bd1 │     312 │ updated 2026…  │
└─────────┴───────────────┴──────────┴──────────┴─────────┴────────────────┘
              Software — explicitly installed
┏━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ Package ┃ Old version ┃ New version ┃ Change       ┃
┡━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ git     │ 2.55.0      │ 2.56.0      │ ↑ upgraded   │
│ vim     │ 9.2.1001    │ 9.2.1164    │ ↑ upgraded   │
└─────────┴─────────────┴─────────────┴──────────────┘
╭────────── Update download estimate ───────────╮
│ Download  9.0 GiB across 1,821 paths          │
│ Unpacked  23.5 GiB                            │
│ Build     408 derivations without a cache hit │
╰───────────────────────────────────────────────╯
```

## Requirements

- Linux with Nix **2.19+** and flakes enabled (`nix-command` + `flakes`)
- Python **3.10+** (only for the `pip` install; the flake provides its own)
- Network access to the flake inputs on first run (the result is cached by Nix)

## Use as a flake input

Add the flake to your inputs and enable the NixOS module:

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    nixflake-checker.url = "github:CeliaMeow2333/nixflake-update-checker";
  };

  outputs = { self, nixpkgs, nixflake-checker, ... }: {
    nixosConfigurations.nixos = nixpkgs.lib.nixosSystem {
      modules = [
        nixflake-checker.nixosModules.default
        { programs.nixflake-check.enable = true; }
      ];
    };
  };
}
```

After `nixos-rebuild switch`, run:

```bash
nixflake-check /etc/nixos
```

The input name (`nixflake-checker`) is up to you — use whatever you put in
`inputs`.

### Run or install without touching your configuration

```bash
nix run github:CeliaMeow2333/nixflake-update-checker -- /etc/nixos
nix profile install github:CeliaMeow2333/nixflake-update-checker
```

### Overlay

```nix
{
  nixpkgs.overlays = [ inputs.nixflake-checker.overlays.default ];
  environment.systemPackages = [ pkgs.nixflake-check ];
}
```

### Exposed flake outputs

| Output | Description |
| --- | --- |
| `packages.<system>.default` / `.nixflake-check` | The `nixflake-check` CLI |
| `apps.<system>.default` / `.nixflake-check` | `nix run` entry point |
| `nixosModules.default` | Adds `programs.nixflake-check.{enable,package}` |
| `overlays.default` | Adds `pkgs.nixflake-check` |
| `devShells.<system>.default` | Python, pytest, rich and nix for development |
| `checks.<system>.default` | Builds the package (tests run in `checkPhase`) |

### Nix development shell

```bash
nix develop
pytest
nix build .#
```

## Install with pip

```bash
cd nixflake-update-checker
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Then run `nixflake-check` (or `python -m nixflake_update_checker`).

For development and tests:

```bash
pip install -e '.[dev]'
pytest
```

## Usage

```bash
nixflake-check                        # uses /etc/nixos if present, else .
nixflake-check /path/to/flake
nixflake-check --host myhost --all
nixflake-check --json report.json --markdown report.md
nixflake-check --only nixpkgs         # update a single input only
nixflake-check --exit-code            # exit 10 when updates are available
nix run github:CeliaMeow2333/nixflake-update-checker -- /etc/nixos
```

| Option | Meaning |
| --- | --- |
| `PATH` | Flake directory (default: `/etc/nixos`, else current directory) |
| `--host NAME` | NixOS configuration to evaluate (auto-detected) |
| `--only INPUT` | Update only this input (repeatable) |
| `--new-lock FILE` | Compare against an existing `flake.lock` instead of fetching upstream |
| `--no-packages` | Skip the version comparison (fast, inputs only) |
| `--no-download` | Skip the download/build size estimate |
| `--all` / `--limit N` | Control how many non-explicit closure components are shown |
| `--json FILE` / `--markdown FILE` | Machine-readable / Markdown export |
| `--sudo` | Use sudo (prompting on the terminal) if the flake is unreadable |
| `--no-github` | Skip GitHub commit-distance lookups |
| `--timeout SECONDS` | Per-command Nix timeout (default 1800) |
| `--exit-code` | Exit code `10` when updates are available |
| `--keep-temp` | Keep the temporary copies for inspection |
| `-v` / `-q` | Verbose Nix output / no spinners |

Set `GITHUB_TOKEN` to avoid anonymous GitHub API rate limits when computing the
“commits behind” column.

## How it works

1. **Snapshot** — the flake directory is copied twice into a temporary
   directory. Your real flake and lock file are never modified.
2. **Update** — `nix flake update` runs in the *new* snapshot. The old snapshot
   keeps the original `flake.lock`, so “before” and “after” are reproducible.
3. **Input diff** — both lock files are parsed and compared (revision, date,
   commit distance for GitHub inputs).
4. **Evaluation** — `nixosConfigurations.<host>` is evaluated for both
   snapshots. This yields the system derivation path, the NixOS label, and the
   explicitly installed packages (`environment.systemPackages`).
5. **Closure extraction** — `nix derivation show -r <system.drv>` lists every
   derivation reachable from the system derivation. Names, `pname` and
   `version` attributes are extracted, fixed-output derivations (tarballs,
   patches, vendored crates) and structural entries (`etc`, `system-path`,
   units, …) are filtered out.
6. **Report** — the two package maps are diffed and shown with
   upgrad
7. **Download estimate** — `nix build --dry-run --no-link` is run against the
   updated flake. Nix consults the configured substituters (including the
   flake's `nixConfig`) and reports the number and size of store paths that
   would be fetched, plus how many derivations must be built locally. Nothing
   is downloaded or built by this step, but the cache lookup can take up to a
   minute; use `--no-download` to skip it.ed/downgraded/added/removed markers, with explicit packages listed
   first.

## About sudo

`/etc/nixos` is usually world-readable, so the default path needs no
privileges. If your flake is unreadable, pass `--sudo`: the program invokes
`sudo -v`, and **sudo itself** prompts for the password on the terminal. The
password is never asked for by this program, never passed as an argument, and
never logged.

## Limitations

- Versions come from the **build-time derivation closure**. That is broader
  than the final runtime closure (it includes compilers, build tools and test
  dependencies) but requires no build and no downloads. Explicitly installed
  packages are therefore listed first; use `--all` to see everything.
- Derivation names/versions are heuristics: `pname`/`version` attributes are
  used when available, otherwise the trailing `-<digits>` part of the name.
  Some packages use non-numeric versions and may not be matched perfectly.
- The tool compares `flake.lock` inputs. Local edits to `configuration.nix`
  are not part of the comparison, and an existing `flak
- The download estimate comes from `nix build --dry-run`, so it reflects your
  configured substituters and current store contents. It covers the new system
  closure, not the flake source tarballs fetched by `nix flake update` itself.e.lock` is required
  (generate one with `nix flake lock`, or pass `--new-lock`).
- The temporary copy excludes `.git`, so a flake that inspects `self.rev` or
  other Git-specific attributes may evaluate differently. `/etc/nixos` is not
  normally a Git repository, so this is rarely relevant.
- Peak memory is roughly 1 GB, almost entirely from `nix eval` evaluating the
  NixOS configuration (our parser peaks around 175 MB).
- Commit distance uses the GitHub API and is informational only; the new
  revision itself always comes from the freshly generated lock file.

## License

MIT
