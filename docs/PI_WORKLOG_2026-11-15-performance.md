# Pi worklog — 2026-11-15 performance round

Scope: verify the previous round against real sources/binaries/raw logs, then optimise
LUT switch/continuous-dial latency and localise the boot-time panel wait. Offline only:
no camera, no card, no flashing, no publish.

## Baseline identity (checked before touching anything)

Every source that the 2026-11-13/14 rounds recorded as delivered still hashes to the
recorded value, and `outputs/metol/nxui.arm` still matched `B2539802…`. So the previous
delivery is intact and nothing else was writing those files.

## 1. Previous-round verification

- `defect-1` (same-value sync FD leak) and the upload/staged-write work: sources unchanged,
  evidence reused.
- `defect-2` (hotspot stop identity): the preserved `workers/report.txt` was the *pre-fix
  sensitivity* run (`identity_change_not_killed` FAIL, SUMMARY 24/1). Re-ran
  `workers_matrix.sh` against the current `mod/deployment/mfw/hotspot.sh` into
  `workers-fixed/report.txt`: **SUMMARY pass=25 fail=0**, including
  `identity_change_not_killed` and `hung_daemon_still_reaped`. That gap is now closed with
  raw output that matches the fixed source.
- UI-25: the recorded tier numbers are reproducible. An independent same-VM run of the
  unmodified logic lands on the same 16 painted frames / 82 ms P50, so the table was not an
  optimistic summary.
- Protected capture-overlap baseline: `runtime/smartfilter_session.inc`,
  `runtime/smartfilter_ram.c`, `runtime/smartfilter_ram_fixture.c` are byte-identical to the
  identity recorded in `outputs/review/2026-11-14-capture-overlap/final/report.json`.
  This round changes none of them.

## 2. LUT switching / continuous dial

Measurement first. Two new trace lines (only under `NX_PANEL_TRACE=1`):

- `PANEL_DRAW ref_ms= evt_ms= draw_ms= damage_us= raster_us= upload_us= raster_px= upload_px=`
- `PANEL_SKIP tick= next_frame= hold= continuous= discrete=` (a dirty frame that did not paint)

and per-frame phase timers in `mod/metol/nxui_ximage.h` (damage scan / raster / rotated
upload), so a slow frame can be attributed instead of guessed.

`tools/probe_panel_latency_vm.py` runs several binaries in one guest with the same
813-press wheel burst, the same 13-step reverse tail and the same trailing discrete key.

What the numbers said: a paint costs ~9 ms (event drain 6–7 ms, draw 1–2 ms, damage 150 µs,
raster/upload 0 ms on the frames whose pixels did not change, ≤23/36 ms on the ones that
did). The 82 ms cadence was not paint cost at all — all 12 skipped frames were hold-gated.
`lut_paint_hold` was armed from `draw_started` (sampled *after* the event drain), so the
deadline landed a few milliseconds after the next frame boundary and every paint wasted a
whole frame.

Fix: arm the hold from the frame deadline (`tick + NX_UI_TARGET_MS`), clamped to
`draw_started + 1` so an overrunning paint still cannot stack paints.

Same VM, same input, final artifacts (`panel-latency-r3-shipped`):

| metric | before | after |
|---|---|---|
| painted frames in the burst | 16 | 27 |
| inter-paint P50 | 82 ms | 41 ms |
| inter-paint P95 | 195 ms | 58 ms |
| longest gap inside the burst | (40 ms cadence) | 58 ms |
| painted during the 25 full batches | 13/25 | 24/25 |
| last input → final paint | 21 ms | 23 ms |
| CPU after first frame | 451 ms | 480 ms |
| wheel presses consumed | 813 | 813 |
| final tune | `[3,0,0,0,0,0,0]`, field 1 | identical |

41 ms is the cadence for *cheap* frames, not a frame rate of the camera body, and the
whole-session longest pause (484 ms) is the idle stretch between the Fn paint and the burst,
i.e. a different window from the 58 ms inside the burst.

### 2b. Unsaturated control workload

The burst saturates the parameter, so most of its paints change no pixels. Added a second
workload (`--workload sweep`): 30 groups of 3 keys alternating direction (triangle wave,
value stays inside −3…0), 80 ms apart, then 3 reverse keys and one KP_Down.

Same VM, both binaries back to back (`panel-sweep-r4`, both variants pass every check):

| metric | before | after |
|---|---|---|
| painted frames / with pixel upload | 34 / 34 | 34 / 34 |
| uploaded pixels | 7.21 M | 7.32 M |
| upload→upload P50 / P95 | 153 / 177 ms | 150 / 194 ms |
| longest upload gap | 403 ms | 417 ms |
| last input → final paint | 6 ms | 21 ms |
| CPU | 2328 ms | 2329 ms |
| draw per frame (median / max) | 32 / 47 ms | 32 / 48 ms |
| wheel presses consumed | 93 | 93 |
| final tune | `[3,0,0,0,0,0,0]` | identical |

So when the picture really changes every frame: a paint costs ~14 ms rasterizing the 359 k reference
frame plus ~13 ms upload (32 ms total, 82-84% of the 40 ms slot), and the schedule fix buys nothing
here. That is not the whole story of the 150 ms interval — see section 3c, which separates draw cost
from the one-skip-per-paint cadence.

Two discarded attempts are kept: a 4-down+4-up group nets to zero inside one frame (no
upload at all), and a single 96-key volley loses events at the X client queue (99 sent,
77 received).

## 3. Boot-time panel wait

Phases are separated by instrument: panel process → visible first frame is 289 ms in the
fixture (`PANEL_INIT`: recipes 16, XOpen 15, window 13, font 18, backend 4, focus snapshot 6,
draw+submit 153, map/focus fence 64). No repeated file read was found there — the reference
table read is already served by the LOOK-CACHE, and `LUT_PREVIEW_READS=4` for a whole session.

The remaining boot tail was the shortcut supervisor's retry gap. `tools/probe_boot_key_vm.py`
withholds the stock window and creates it at swept offsets, reading readiness from the
service (`custom_key.state` + `NX_CUSTOM_BOOT`/`NX_CUSTOM_RETRY` lines), not from a LUT file:

| retry policy | window→ready samples (ms) | worst | median (mean of 3rd/4th) |
|---|---|---|---|
| 1000 ms for every boot reason | 170, 200, 250, 331, 810, 820 | 820 | 290.5 |
| 100 ms for `window-unavailable`, 1000 ms for context/X | 210, 250, 330, 370, 380, 400 | 400 | 350 |

The tail halves; the median moves the wrong way by 60 ms and six samples cannot resolve that,
so only the tail claim is made.

## 3b. Session gate: the earlier diagnosis was wrong

The previous round reported that this suite "aborts in its RAM tail because the fixture has already
exited". That was not what the log says. The first real error in
`outputs/deployment/smartfilter-session/arm-vm/execution.json` (output line 262, right after the
binding-libc block finished) is

```
stat: can't stat '/tmp/nx300m-lut-panel.GuFq1j/mfw-font.charset': No such file or directory
```

raised by the probe's own panel-asset pin block, which still created and demanded a
`fonts/mfw-font.charset` pin. Production `lut-panel.sh` dropped that asset: it hard-links only
`bin/nxui.arm` and `bin/lut_engine.arm` and runs on the stock firmware font, passed as `--font-file`.
The suite aborted there, so nothing after it ran; the RAM fixture had answered request 12
(`command 105`, the *request references* step), not a final-references request, and the fixture was
never shown to exit early.

The pin block is now `PANEL_PIN_CONTRACT` in `tools/probe_ipcs_session_vm.py` and checks the current
contract instead of the retired one:

1. both staged binaries are hard links to the private runtime copies (same inode),
2. the pinned generation stays usable after the resident pathname is replaced,
3. the launcher really runs `--engine <runtime>/lut_engine.arm`, `--font-file <stock font>`,
   `--recipes <root>/looks.txt` and `--dry-run` (the stub records the argv it was exec'd with),
4. no `--font-metadata` argument is passed any more (the charset asset is retired),
5. with the firmware font masked by a bind mount, the launcher refuses to start with
   `original firmware font unavailable` (the mask is restored in the same block).

Every check writes `pin_check=<name> ok|failed expected=[…] actual=[…]`, so a failure names itself
instead of exiting silently. The shared block is exercised first by `tools/probe_panel_pin_vm.py`
(`panel-pin-r6`: passed, 9 checks ok, `font_dependency=enforced`) and only then by the suite.

Re-running the suite serially against ABI7/build 345225401 **passes**: exit 0,
`SMARTFILTER_ARM_BOUNDARY_PASSED`, `actual_ram_loader=true`,
`baseline_candidate_baseline_byte_identical=true`, `private_vm_stopped=true`
(`outputs/deployment/smartfilter-session/arm-vm/report.json`). The 2026-11-04 pass is preserved at
`arm-vm-before-capture-overlap/`. No protected runtime file was touched and no threshold, `passed`
flag or recorded hash was edited.

Note while verifying: `fonts/mfw-font.charset` is still shipped by `build_mfw_services.py`,
`build_lut_mod.py` and `release_payload.py`, and `nxui` still *accepts* `--font-metadata`, but the
option is never read (only parsed and validated against `--font-file`). It is dead weight, not a
dependency — which is exactly why the pin was removed rather than restored.

## 3c. Performance: the 32 ms / 150 ms gap

The previous text said the unsaturated workload was "paint-cost bound". The measured numbers do not
support that phrasing, so it is replaced with what the traces show:

- draw is p50 32 ms / mean 32.8-33.8 ms against the 40 ms `NX_UI_TARGET_MS` slot — 82-84% of one slot;
- each run holds 34 `PANEL_DRAW` and 31 `PANEL_SKIP` lines, and the skips between two consecutive
  paints are 1 (31 times) and 0 (4 times): one frame is gated after almost every paint, all of them
  `continuous=1`;
- one skip per paint means the cadence is two slots = 80 ms, which is below the observed ~150 ms.

So draw cost alone would allow 80 ms. The remaining ~70 ms tracks the workload's own injection
period (30 key groups paced 80 ms apart plus per-key driver cost), which this workload does not
measure independently. Conclusion: the 150 ms is a one-skip-per-paint cadence following the injected
input rate, not evidence about the panel ceiling under continuously pending input, and the 41 ms
burst figure stays a cheap-frame cadence. Measuring the real ceiling needs a workload that keeps
inputs pending without saturating the value, plus per-frame tick/deadline trace; that is not this
round's task and no refactor was done on the strength of the wrong phrasing.

## 3d. The settings page is dynamic — the debug entry was there all along

The earlier claim that "no menu file carries a debug action, so the debug menu is unreachable" was
wrong. `mod/metol/nxui_feature_pages.h` `feature_page(page_id==5)` builds the settings page at
runtime and ends with

```c
feature_add("模块状态","modules","查看",1);
feature_add("调试与日志","debug","进入",1);
```

so the page has seven rows (index 0 fw-mode-lock … 4 MOD autostart, 5 modules, 6 debug). Searching the
static `gui*.NX300M` files cannot see them.

The gate failed because it walked four `XF86ScrollUp` detents from index 0, which lands on index 4 — the
autostart row — and then pressed Return, executing that action instead of opening the debug page. The
navigation now enters settings deterministically, walks detents until `MFW_SELECT page=5 index=6
action=debug` appears, presses Return, requires `MENU file=…/gui_debug.NX300M items=9`, and requires
MENU back to the settings row and then to the root list. The no-auto-action assertions are kept.

The final preview assertion also had to follow the product: `nxui_preview_state.h` `nx_preview_action()`
returns `preview-stop` / `preview-preference` / `preview-s2-enable` depending on state, so a panel with
no applied LUT records exactly one `preview-preference` and must not start or stop hardware preview.
The gate now asserts that, plus "not preview-stop/s2-enable", instead of the retired `preview-toggle`
name.

## 3e. A real spin in the hidden apply wait

The gate requires the panel to sleep while a control backend runs. Measured: the stored passing run had
`wakeups=22`, the current panel reported `wakeups=5621` with `zero_delay=5620` in 2223 ms — a genuine
zero-timeout `select` loop, not a fixture mistake (the baseline binary is expected to spin, `>1000`).

Attribution came from instrumenting the timeout itself: `EXIT` now reports `zero_delay=<n>` and a
`PANEL_SPIN` line naming the term that forced the minimum. It said `reference_due=5526`.

Root cause: the timeout was lowered to the coalesced reference-rebuild deadline even when that deadline
had already expired and the rebuild could not run because a backend child owned the engine. Fix: only
use the rebuild deadline when the rebuild can actually run (`!child && !queue.commit_requested`) and the
deadline is still in the future; the child reap tick is the wake source until then. The same "deadline
must be ahead" guard now applies to the paint deadline.

After the fix: `wakeups=26`, `zero_delay=0`, baseline still `4449`. Fn/OK/MENU, the backend cover and the
hidden/masked paths are untouched, and the gate's thresholds were not changed.

## 3f. Gates had to be re-run, and running them exposed stale contracts

Every panel gate compares the hash of the artifact it measured with the current binary, so a comment-only
rebuild invalidates all three. They were re-run (not edited), and running them was what surfaced the real
problems:

1. `probe_lut_selection_vm.py` compiled its fixture with `-Werror` against the shared LUT headers, where
   `nx_table_cache_summary` / `nx_table_cache_invalidate` are unused in that translation unit. The fixture
   now builds with `-Wno-unused-function`; production headers untouched.
2. The same fixture asserted `.state/lut-favorites`, but favourites moved to checksummed `NXFAV1` records in
   dual slots (`.0`/`.1`) with the legacy file only read for old cards. It now verifies the record the panel
   actually writes.
3. `probe_lut_tune.py` had the same `-Werror` problem, and its report is what the packager uses for the
   `lut-cycle.sh` hash, so it was re-run after that script changed.
4. The groups scenario hardcoded `index=2` for `nx300-hard-mono` and allowed ten presses to reach the category
   bar. The catalogue is a build artefact regenerated by the packaging entry point (`build_lut_color_test`),
   so its order is not a contract: the packager run moved mono from index 2 to 68. The scenario now asserts
   what the detent means — the selection moves one row up, its index decreases by one, its key changes — and
   allows a full wrap into the category bar.
5. The full-catalogue check required `keys-from-off` and `tap-from-off` to land on `nx300-hard-mono`, which
   held only while mono was the first catalogue entry. Those two variants now require one recorded apply of
   the current first entry (key and touch must agree), while the two restore variants keep the exact mono
   assertion.

Each of those is a stale test contract, not a product defect: the panel behaviour was right, the fixture was
pinned to a catalogue order that the build regenerates.

## 4. Still hook — research only

`runtime/lut_ram_blob.c` publishes once per completed three-channel triplet
(`if(s->forwarded==7){ stat_ipcs_completes++; publish(s); }`). That binds a publish to triplet
completion; it does not show that consecutive published states contain nothing deduplicable.
The fixture counters (561 updates = 561 publishes) only say the fixture completes one triplet
per update. Whether the stock camera republishes identical channel data is unmeasured, so no
dedup is enabled and the hook is untouched. The instrument already exists:
`stats.updates / ipcs_completes / publishes` in `/var/run/nx300m-lut-status.json` (a Still-hook
file, not a shortcut-ready interface).

## 5. Packages

- `tools/build_mfw_services.py` rebuilt the base services kit: 59 members, CRC clean,
  packaged `nxui.arm` / `nx-remote-controller.arm` byte-identical to `outputs/metol/…`.
  **The kit is a base floor, not a mod update**: it has no `ipcs-diagnostic/lib/smartfilter_ram.bin`,
  no `smartfilter-preview.sh`, no `bin/lut_engine.arm` and no `mfw-panel.sh`, so it cannot carry the
  capture handover.
- `scratch/build-capture-overlay.py` builds a **declared incremental overlay**:
  `outputs/deployment/capture-overlay/nx300m-capture-overlay.zip`, 36 files, CRC clean, sha256
  `1d22dc09…`, containing the capture-overlap backend (ABI7, build 345225401, sha256 `5f669d93…`)
  with its paired loader, the current `nxui.arm`/`nx-remote-controller.arm`, `lut_engine.arm`,
  `lut_live_loader.arm` + `lut_ram_blob.bin`, `smartfilter-preview.sh`, `mfw-panel.sh`, the
  panel/action scripts and menus. The builder writes fixed zip timestamps, so the same inputs give the
  same archive and the recorded sha256 describes the contents rather than the build moment.
- **The overlay ships the merged manifest, and that was verified by running the real stager.**
  `mod/deployment/mfw/ram-stage.sh` copies exactly the entries of `runtime-files.list` into the RAM
  runtime the services execute from. `tools/probe_overlay_ram_stage_vm.py` applies the overlay to an
  installed tree (connected kit + LUT-mod layers, 158 files), runs the production script in an ARM
  guest with a tmpfs RAM target, and compares every staged entry byte for byte:
  **80/80 entries staged, 0 missing, 0 differ, staged backend sha `5f669d93…`**
  (`overlay-ram-stage-r5`). The manifest string alone would have proved nothing.
  Re-run after the final rebuild: `overlay-ram-stage-r6`, same result.
- **The full package now builds.** `tools/prepare_ipcs_diagnostic_card.py prepare()` produced
  `nx300m-ipcs-open-diagnostic.zip`: 797 files, ABI 7, build 345225401, session mode 1,
  252 catalogue looks, paired still components, `nxui.arm` `abb80bbf…` equal to `outputs/metol`.
  Its boot chain is closed by `deploy()`, which writes `runtime-files.list` as the card manifest plus
  every package file that is not a look, catalogue, `.state` record or markdown document, and adds a
  `RESTART_SERVICE` stamp. That path needs a mounted card, so it was replayed offline instead: the
  same merge rule over the package yields 81 entries, and the real `ram-stage.sh` stages
  **81/81 entries with 0 missing, 0 differ and the same backend hash** (`package-ram-stage-r3`).
  Not done: no card was written and `deploy()` itself was not executed.

## 6. Report and comment corrections

- Fn semantics corrected: Fn saves the displayed look's preset and stays in the panel (no apply, no
  exit); OK saves/applies and returns to the viewfinder; MENU keeps its previous meaning.
- Boot medians restated with an explicit definition (mean of the 3rd/4th of six samples:
  290.5 → 350 ms), and the earlier upper-median variant (331 → 370) is recorded as such.
- `/var/run/nx300m-lut-status.json` is a Still-hook status file, not the Custom-shortcut ready
  interface; readiness comes from the remote status endpoint and the supervisor log.
- Still-hook wording corrected as described in section 4.
- Gap windows separated (58 ms inside the burst vs 484 ms across the whole session) and 41 ms
  described as a software paint cadence, not a body frame rate.
- `mod/metol/nxui.c`: the three comments that still described a ~15 Hz burst cadence / 15 Hz
  defer / extra 33 ms after a slow draw now describe the current 40 ms target scheduling and the
  paint-cost limit. The edit changed no scheduling code: `.text`, `.rodata`, `.data` of the rebuilt
  binary are byte-identical to the previously reviewed build (`scratch/compare-nxui-text.sh`,
  19ABD53C vs 7DF83B12), while the whole-file hash necessarily differs.
