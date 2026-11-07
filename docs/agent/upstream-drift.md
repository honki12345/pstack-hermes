# Runbook — upstream drift (re-pinning)

> [!IMPORTANT]
> Upstream (`cursor/plugins` → `pstack`) is **pinned**. The port is faithful to
> that SHA, not to upstream `HEAD`. Drift is expected and handled here; it is
> never fixed by hand-editing generated package surfaces (they are wiped on the
> next build).

Machine check: `tools/check_pins.py` fails CI when `UPSTREAM_PIN` is missing,
duplicated, malformed, or disagrees across declaring workflows.

## How drift surfaces

Any of these means upstream moved past the pin (or the pin is inconsistent):

- **The build fails loudly** — `CONVERT ERROR: anchor audit: N transform
  anchor(s) never matched`. Primary detector: an anchor written against the
  pinned text no longer matches, so the transform would silently no-op. The
  build refuses to produce a package.
- **The "Verify pinned SHA" / reproducibility step** in CI fails when the
  checked-out upstream clone's SHA no longer equals `UPSTREAM_PIN`, or when the
  committed package is not reproducible from that pin.
- **Manual inspection** — `git -C <pstack-clone> log --oneline <pin>..HEAD --
  pstack/` (or the upstream path that holds the plugin).

> [!NOTE]
> The former weekly `upstream-drift-watch` workflow was removed with the
> root-layout migration. Re-pin still depends on anchor audit + pin lock-step +
> human/CI rebuild. Model slug drift is checked by hand with
> `tools/slug_drift.py`.

## Procedure

1. **Fetch and diff.** Update the local upstream clone, then diff the new SHA
   against the pin — focus on skills, agents, and the upstream manifest:
   `git -C <pstack-clone> log --oneline <pin>..HEAD -- .`
2. **Decide the new pin.** Read the upstream commits. If they touch text any
   map anchors on, they are in scope; unrelated files may be ignored.
3. **Update the maps, not the package.** For each dead anchor:
   - re-anchor to the new upstream text and keep the intended replacement, or
   - prune the entry if upstream already made the change, and record why in the
     commit body.
   Edit `tools/convert.py` only (plus `tools/assets/` when the asset must move).
4. **Rebuild and let the gates run.**
   `uv run --frozen tools/convert.py --source <pstack-clone>`
   must exit 0 with a clean anchor audit (default `--out` is the repo root).
5. **Validate.** `uv run --frozen tools/validate.py` — static + gold stages
   that apply must pass.
6. **Re-check package docs.** If the change touches what Differences or Known
   limitations describe, update the README template in `convert.py` and rebuild.
7. **Update `UPSTREAM_PIN` everywhere it is declared.** Today that is
   `ci.yml` only. The converter holds
   no pin: it records the SHA of the clone it is pointed at, so `source_commit`
   in `.build-provenance.txt` follows the checkout. Adding a workflow that
   builds from the pin means adding it to `REQUIRED_DECLARERS` in
   `tools/check_pins.py` and to this list.
8. **PR** with `rebuilt from pin <sha>` in the body (PR template fields) and a
   one-line note per map change. Link an adaptation issue when maps/assets
   changed shape.

## If upstream disappears

If `cursor/plugins` is deleted or moves, point the build action's
`upstream-url` input at a mirror that still contains the pinned commit (keep
`upstream-pin` unchanged) in every workflow that declares `UPSTREAM_PIN` — the
port then continues from the frozen pin. Users are never affected: installs
and updates come from this repository, not upstream.

## Do not

- **Do not hand-edit generated package surfaces.** Edits vanish on rebuild.
- **Do not delete a dead anchor without checking its intent.** "Never matched"
  can mean *upstream reworded* (re-anchor) or *upstream already fixed it*
  (prune) — decide per entry and say which in the commit body.
- **Do not silence the audit.** There is no force flag; the audit exists so a
  silently no-op transform cannot ship.
- **Do not fold unrelated changes into a re-pin.** Keep map updates and pin
  bumps reviewable on their own.

## Rollback

A bad rebuild only affects the branch: the build uses a temp sibling, but
rebuilding with `--out` set to the repo root is **not atomic** — entries are
removed and copied individually, so readers may observe a partial tree and an
interrupted rebuild is not guaranteed to restore the prior tree. A failed build
does not replace a good tree, but an interrupted in-place rebuild may leave one. To revert a landed pin bump, revert the PR — the package
is regenerated from the previous pin.
