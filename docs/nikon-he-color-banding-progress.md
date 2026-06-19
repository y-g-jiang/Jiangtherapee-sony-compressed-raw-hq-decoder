# Nikon HE/HE* color banding progress

Date: 2026-06-04

## Goal

Fix the color/chroma banding and wrong-color reconstruction for these Nikon HE/HE* targets:

- Z8 HE
- Z6III/Z63 HE
- Z8 HE*
- Z6III/Z63 HE*
- Z9 HE

Keep this file updated with major progress so context compaction does not lose the debugging path.

## Working trees and tools

- Research repo: `C:\Users\bcm18\Downloads\Jiangtherapee-sony-craw-hq-decoder`
- LibRaw decoder tree: `C:\Users\bcm18\Music\4knewjiangtherapee\tools\LibRaw-nikon-he-star`
- Local sample folder: `C:\Users\bcm18\Music\4knewjiangtherapee\samples\nikon-he-star`
- MSBuild: `C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\MSBuild\Current\Bin\amd64\MSBuild.exe`
- VC vars: `C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars64.bat`
- Python: `C:\Users\bcm18\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`

## Local sample coverage

Found local target-like samples:

| Local file | Role |
|---|---|
| `Z8_HE_low.NEF` | Z8 HE |
| `Z8_HE_high.NEF` | Z8 HE* by size/Bp evidence |
| `Nikon_Z6__3_High_Efficiency_FX.NEF` | Z6III/Z63 HE |
| `Nikon_Z6__3_High_Efficiency_Star_FX.NEF` / `Z6III_HE_Star_FX.NEF` | Z6III/Z63 HE* |
| `Z9_HE.NEF` | Z9 HE |
| `Z9_HE_Star.NEF` | extra Z9 HE* regression |

Plain Z6III/Z63 HE coverage is now present via raw.pixls `Nikon_Z6__3_High_Efficiency_FX.NEF`.

## Already fixed

The old black horizontal tile-boundary rows are fixed in the local LibRaw tree. The landed tile orchestration constants are:

- `kFirstTileX1Offset = 4`
- `kLaterTileX1Offset = 12`
- `kBoundaryCarryStripes = 3`
- `kOverflowSaveStartStripe = 26`
- image-wide `overflow` enlarged to `3 * stripe_ints`

Verification record: `out\nhe_tile_experiments_20260604\final_verification.md`.

The final tile-boundary validation reported zero detected horizontal black-line rows for:

- `Nikon_Z8_high_efficiency_high.NEF`
- `Nikon_Z8_high_efficiency_low.NEF`
- `Z6III_HE_Star_FX.NEF`
- `Z8_HE_high.NEF`
- `Z8_HE_low.NEF`
- `Z9_HE.NEF`
- `Z9_HE_Star.NEF`

## Current failure

The remaining failure is not the old zero-row problem. It is wrong color/chroma reconstruction with visible horizontal color bands.

Baseline run:

`out\nhe_color_band_fix_20260604\baseline_final_branch`

Metrics script:

`tools\measure_nikon_he_color_banding.py`

Current baseline summary from `color_metrics.json`:

| Sample | Raw local chroma bad rows | RGB chroma bad rows | RGB zero bad rows | Visual state |
|---|---:|---:|---:|---|
| `Z8_HE_low.NEF` | 3 | 115 | 0 | strong magenta/pink cast and bands |
| `Z8_HE_high.NEF` | 0 | 40 | 0 | dark/wrong color, visible bands |
| `Z6III_HE_Star_FX.NEF` | 8 | 16 | 0 | wrong color plus clustered bands |
| `Z9_HE.NEF` | 1 | 64 | 0 | large wrong-color regions and bands |
| `Z9_HE_Star.NEF` | 0 | 0 | 0 | best/cleanest regression sample |

Contact sheet:

`out\nhe_color_band_fix_20260604\baseline_final_branch\contact_sheet.png`

## Updated diagnosis

The major wrong-color/chroma banding failure was caused by incomplete GTLI coverage, not by the old tile-boundary carry bug.

Local bitstream probing showed that failing regular HE files contain many `(Bp, Br)` pairs missing from the static GTLI table. The old decoder silently fell back to GTLI zero for those rows, which over-decoded truncated coefficient bitplanes and produced large color/banding errors. The cleanest regression sample, `Z9_HE_Star.NEF`, had `unknown_count=0` against the static table.

For explicit regular HE rows with `Bp=4/5`, every static GTLI row is exactly fit by this per-subband model:

`gtli = max(0, Bp - gain[sb] - (refinement[sb] < Br ? 1 : 0))`

The HE* rows with `Bp=1/2/3` do not fully fit this formula, so explicit static table rows must continue to win first. The local fix only generates fallback rows after the static lookup misses.

Implementation in the local LibRaw tree:

`C:\Users\bcm18\Music\4knewjiangtherapee\tools\LibRaw-nikon-he-star\src\decoders\nikon_he\nikon_he_gtli_table.cpp`

- Keep the existing static table for known HE and HE* rows.
- Add generated fallback rows for missing `Bp >= 3 && Bp <= 15` and `Br in [0,63]`.
- Use fitted arrays:
  - `kHeGtliGain = {3,3,2,2,1,1, 2,1,1,0,0,0, 0, 2,1,1,0,0,0, 1,0, 0,0, 0, 0,0}`
  - `kHeGtliRefinement = {2,16,7,22,7,21,5,3,16,1,7,13,11, 6,4,16,0,7,14,20,16,15,23,11,12,24}`

Static verification: all explicit regular HE rows with `Bp>=4` still match the formula exactly, and all `(Bp, Br)` combos observed in the local target sample set are now covered.

The older MCT/gain hypothesis is still a possible explanation for residual edge/local artifacts, but it is no longer the primary explanation for the large color bands.

## Latest GTLI fallback validation

Build targets completed in the local LibRaw tree:

- `buildfiles\libraw.vcxproj`
- `buildfiles\unprocessed_raw.vcxproj`
- `buildfiles\simple_dcraw.vcxproj`

MSBuild needed these local overrides because the project expects older defaults:

- `/p:WindowsTargetPlatformVersion=10.0.26100.0`
- `/p:PlatformToolset=v145`

Validation run:

`out\nhe_color_band_fix_20260604\gtli_formula_fallback`

Commands used per sample:

- `unprocessed_raw.exe -q <file.NEF>`
- `simple_dcraw.exe -T <file.NEF>`

Metrics and contact sheet:

- `out\nhe_color_band_fix_20260604\gtli_formula_fallback\color_metrics.json`
- `out\nhe_color_band_fix_20260604\gtli_formula_fallback\contact_sheet.png`

Latest RGB chroma row results:

| Sample | Baseline RGB chroma bad rows | GTLI fallback RGB chroma bad rows | Residual notes |
|---|---:|---:|---|
| `Z8_HE_low.NEF` | 115 | 0 | major color/banding issue fixed |
| `Z8_HE_high.NEF` | 40 | 2 | one RGB zero-bad row at 4130, chroma rows 4126/4134 |
| `Z6III_HE_Star_FX.NEF` | 16 | 2 | residual top-row chroma rows 1/2 |
| `Z9_HE.NEF` | 64 | 1 | residual top-row chroma row 2 |
| `Z9_HE_Star.NEF` | 0 | 0 | remains clean |

Visual contact sheet confirms the large Z8/Z9 wrong-color regions are substantially reduced. Remaining artifacts appear localized around high-contrast edges or first rows, not the previous image-wide chroma banding.

## 2026-06-04 residual audit update

Tooling updates in the research repo:

- Added `tools\probe_nikon_he_precincts.py`.
  - Walks Nikon JPEG-XS raw strips using the same `size_minus_prefix + 12` and 6-byte alignment rule as the C++ decoder.
  - Reports `Bp/Br` distribution, strip bpp, and whether the strip actually starts with the JPEG XS `ff 10 ff 50` marker.
  - Classifies HE/HE* by strip bpp (`~3` for HE, `~5` for HE*), because first `Bp` alone misclassifies samples such as `Z9_HE.NEF`.
- Extended `tools\measure_nikon_he_color_banding.py`.
  - Keeps the old fields for baseline comparison.
  - Adds border-excluded row counts (`*_interior_*`) so first/last-row interpolation artifacts do not masquerade as image-wide color bands.
  - Adds contiguous zero-run and bad-row run summaries to distinguish whole-row bands from sparse dark texture/high-contrast content.

New v2 metrics:

- `out\nhe_color_band_fix_20260604\gtli_formula_fallback\color_metrics_v2.json`
- `out\nhe_color_band_fix_20260604\gtli_formula_fallback\contact_sheet_v2.png`
- `out\nhe_color_band_fix_20260604\precinct_summary_all.json`

V2 residual interpretation:

| Sample | JPEG XS? | Variant by strip bpp | RGB bad rows | RGB interior bad rows | Notes |
|---|---|---|---:|---:|---|
| `Z8_HE_low.NEF` | yes | HE | 0 | 0 | clean for RGB row chroma bands |
| `Z8_HE_high.NEF` | yes | HE* | 2 | 2 | rows 4126/4134 are isolated around high-detail foliage/water; the row-4130 zero hit has max contiguous zero run only 28 px of 8280, not a whole horizontal band |
| `Z6III_HE_Star_FX.NEF` | yes | HE* | 2 | 0 | only top rows 1/2; no interior RGB chroma bands |
| `Z9_HE.NEF` | yes | HE | 1 | 0 | only top row 2; no interior RGB chroma bands |
| `Z9_HE_Star.NEF` | yes | HE* | 0 | 0 | clean |

Important sample correction:

The downloaded Photography Blog `nikon_z6_iii_01.nef` through `nikon_z6_iii_05.nef` files are **not** valid Z6III HE/HE* validation samples for this decoder path. They are Z6III NEFs, but their raw strips do not contain the JPEG XS `ff 10 ff 50` marker and `probe_nikon_he_precincts.py` reports `inferred_variant = not Nikon HE/HE* JPEG-XS stream`. They were decoded successfully by LibRaw via another Nikon path, so their metrics cannot prove or disprove the Nikon HE/HE* decoder.

Historical note from the residual audit: before the raw.pixls update, plain Z6III/Z63 HE was not yet verified. The local `Z6III_HE_Star_FX.NEF` is JPEG-XS but probes at about `5.0` bpp in the final audit, so it should be treated as the Z6III/Z63 HE* coverage sample, not plain HE.

## Current source instrumentation

The local LibRaw tree now has default-off diagnostics in:

- `src\decoders\nikon_he\nikon_he_decode.cpp`
- `src\decoders\nikon_he\nikon_he_bayer.cpp`
- `src\decoders\nikon_he\nikon_he_bayer.h`

Environment variables:

- `LIBRAW_NIKON_HE_STEP1_ORDER=1032`
  - tokens: `0=LH`, `1=LL`, `2=HL`, `3=HH`
  - default: `1032` means `LL,LH,HH,HL`
- `LIBRAW_NIKON_HE_STEP2_ORDER=1LH2`
  - tokens: `0=LH`, `1=LL`, `2=HL`, `3=HH`, `L=step1_L`, `H=step1_H`
  - default: `1LH2` means `LL,L,H,HL`
- `LIBRAW_NIKON_HE_ROW_MODE=tile`
  - clamps row neighbors inside a tile for testing
  - default keeps current cross-tile neighbor reads
- `LIBRAW_NIKON_HE_DEBUG=1`
  - prints selected mapping and row mode

Defaults preserve current decoder behavior.

## Next concrete actions

1. Inspect `Z8_HE_high.NEF` rows 4126/4134 against embedded JPEG or camera JPEG to decide whether the isolated local chroma metric is real decoder damage or scene/high-contrast content.
2. If the top-row artifacts matter for final output, handle first-row reconstruction/cropping explicitly; they are no longer evidence of interior color banding.
3. Continue searching for a real JPEG-XS Z6III/Z63 HE sample; reject generic Z6III NEFs unless the raw strip starts with `ff 10 ff 50`.
4. Once the Z8 HE* local artifact is classified, rerun the five confirmed JPEG-XS samples and record a final gate.

## 2026-06-04 tile-boundary carry sweep

The remaining Z9/Z9* horizontal red/green artifacts are still tied to the 64-row tile cadence, especially raw adjacent-row edges near row mod64 `5/6` and `63/0`. I added default-off runtime controls in the local LibRaw tree so tile carry hypotheses can be swept without rebuilding:

- `LIBRAW_NIKON_HE_BOUNDARY_CARRY_STRIPES`
- `LIBRAW_NIKON_HE_FIRST_TILE_X1_OFFSET`
- `LIBRAW_NIKON_HE_LATER_TILE_X1_OFFSET`
- `LIBRAW_NIKON_HE_OVERFLOW_SAVE_START_STRIPE`

The overflow buffer allocation was raised to `kMaxBoundaryCarryStripes = 8`; the compiled defaults now remain conservative:

- boundary carry stripes: `3`
- first tile x1 offset: `4`
- later tile x1 offset: `12`
- overflow save start stripe: `29`

Validation artifacts:

- `out\nhe_color_band_fix_20260604\tile_param_sweep\coarse_z9_raw_sweep.json`
- `out\nhe_color_band_fix_20260604\tile_param_sweep\cross_sample_raw_candidates.json`
- `out\nhe_color_band_fix_20260604\tile_param_sweep\cross_sample_raw_carry_ext2.json`
- `out\nhe_color_band_fix_20260604\tile_param_sweep\final_default_3_29_12`
- `out\nhe_color_band_fix_20260604\tile_param_sweep\final_default_3_29_12_metrics.json`
- `out\nhe_color_band_fix_20260604\tile_param_sweep\final_default_3_29_12_thumb_compare.json`

Raw same-color adjacent-row tile-boundary score, lower is better, over the five confirmed JPEG-XS samples:

| Variant | Params `carry/save/later` | Mean raw boundary score | Bad target edges >500 | RGB interior bad rows |
|---|---:|---:|---:|---:|
| previous default | `3/26/12` | 726.3 | 149 | 2 |
| new default | `3/29/12` | 576.4 | 81 | 2 |
| aggressive raw best | `6/28/24` | 328.4 | 10 | 12 |

The aggressive `5/28/20`, `6/28/24`, and `6/29/24` variants greatly reduce the raw tile-boundary score but introduce new RGB interior chroma rows in the Z6III grass sample around the first tile boundary. They should remain diagnostic only until the vertical IDWT x2/x3 carry model is understood better.

Final no-env default validation after rebuilding produced bit-identical PGM/TIFF outputs to the swept `3/29/12` candidate. RGB interior bad rows:

| Sample | RGB interior bad rows | Raw local bad interior rows | Preview bad diff rows |
|---|---:|---:|---:|
| `Z6III_HE_Star_FX.NEF` | 0 | 0 | 27 |
| `Z8_HE_high.NEF` | 2 | 0 | 1 |
| `Z8_HE_low.NEF` | 0 | 0 | 1 |
| `Z9_HE.NEF` | 0 | 0 | 2 |
| `Z9_HE_Star.NEF` | 0 | 0 | 1 |

Interpretation at that checkpoint: moving the overflow save start from stripe 26 to 29 was a safe conservative improvement for the tile-period raw edge anomaly without increasing the RGB row-band gate. The stronger carry/offset variants were useful evidence that the real remaining issue was likely vertical-lift cross-tile state, not GTLI or color conversion alone.

## 2026-06-04 final tile-head phase update

New runtime control added in the local LibRaw tree:

- `LIBRAW_NIKON_HE_MEMCPY_START_STRIPE`

The focused parallel sweep tested `carry in {2,3,4}`, `save in {27,28,29,30}`, `later_x1 in {8,12,16}`, and `memcpy_start in {0,1,2,3,4}` across:

- `Z9_HE.NEF`
- `Z8_HE_low.NEF`
- `Z8_HE_high.NEF`
- `Z6III_HE_Star_FX.NEF`

Focused sweep artifact:

- `out\nhe_color_band_fix_20260604\parallel_tile_sweep_20260604\focused\focused_raw_phase_sweep.json`

Best focused raw phase candidate:

- `carry=3`
- `later_x1=12`
- `overflow_save=28`
- `memcpy_start=4`
- mean phase score: `588.39`
- max phase score: `661.91`
- bad phase count: `184`

For comparison, the earlier `3/29/12` default with `memcpy_start=0` was about:

- mean phase score: `649.30`
- max phase score: `777.46`
- bad phase count: `290`

Source defaults now landed in:

`C:\Users\bcm18\Music\4knewjiangtherapee\tools\LibRaw-nikon-he-star\src\decoders\nikon_he\nikon_he_tile.cpp`

Current defaults:

- boundary carry stripes: `3`
- first tile x1 offset: `4`
- later tile x1 offset: `12`
- overflow save start stripe: `28`
- later-tile memcpy start stripe: `4`

MSBuild Release x64 succeeded with `0` warnings and `0` errors after the source-default change.

Final no-env default validation:

- `out\nhe_color_band_fix_20260604\parallel_tile_sweep_20260604\final_default_save28_mem4`
- `out\nhe_color_band_fix_20260604\parallel_tile_sweep_20260604\final_default_save28_mem4_metrics.json`
- `out\nhe_color_band_fix_20260604\parallel_tile_sweep_20260604\final_default_save28_mem4_thumb_compare.json`
- `out\nhe_color_band_fix_20260604\parallel_tile_sweep_20260604\final_default_save28_mem4_contact.png`
- `out\nhe_color_band_fix_20260604\parallel_tile_sweep_20260604\final_default_save28_mem4_thumb_sheet.png`

Hash check: the fresh no-env default PGM/TIFF outputs are SHA-256 identical to the earlier env-controlled `rgb_top_save28_mem4` candidate for all five validated files.

Final row-band summary:

| Sample | Variant by strip bpp | Baseline RGB bad rows | Final RGB bad/interior rows | Final raw local bad interior rows | Final RGB zero interior rows |
|---|---|---:|---:|---:|---:|
| `Z8_HE_low.NEF` | HE | 115 | 0/0 | 0 | 0 |
| `Z8_HE_high.NEF` | HE* | 40 | 2/2 | 0 | 1 |
| `Z6III_HE_Star_FX.NEF` | HE* | 16 | 2/0 | 0 | 0 |
| `Z9_HE.NEF` | HE | 64 | 1/0 | 0 | 0 |
| `Z9_HE_Star.NEF` | HE* | 0 | 0/0 | 0 | 0 |

Embedded-preview comparison is effectively neutral versus the previous `3/29/12` default:

| Sample | Preview bad rows old -> final |
|---|---:|
| `Z6III_HE_Star_FX.NEF` | `27 -> 27` |
| `Z8_HE_high.NEF` | `1 -> 1` |
| `Z8_HE_low.NEF` | `1 -> 1` |
| `Z9_HE.NEF` | `2 -> 2` |
| `Z9_HE_Star.NEF` | `1 -> 1` |

Z8 HE* residual row audit:

- Rows `4126/4134` remain isolated RGB chroma-threshold hits.
- Row `4130` remains an isolated RGB zero-row threshold hit, but the maximum contiguous zero run is only `28` pixels out of `8280`, not a full-width horizontal band.
- Local x10 crops in `final_default_save28_mem4\z8_hestar_row4130_inspect` do not show a hard full-width horizontal decode band; the rows sit in high-detail dark foliage/water/edge content.
- Current interpretation: these are local scene/high-contrast threshold hits, not the original color-band failure.

Sample coverage correction:

- raw.pixls `Nikon_Z6__3_High_Efficiency_FX.NEF` is a verified Z6III/Z63 HE JPEG-XS sample: SHA-256 `9ae7346adb3b16f011d1d3c11b4c7ae2ab0231c64111dff3fad8e28dd55353c8`, strip bpp about `3.0`, `inferred_variant=HE`.
- raw.pixls `Nikon_Z6__3_High_Efficiency_Star_FX.NEF` is a verified Z6III/Z63 HE* JPEG-XS sample: SHA-256 `10eea35531eb5427c171ab5158d064b79844a9fe039f8ca971b20c4f3be36cfd`, strip bpp about `5.0`, `inferred_variant=HE*`.
- `Z6III_HE_Star_FX.NEF` is byte-equivalent in behavior to the raw.pixls HE* FX sample and should be treated as HE* coverage.
- The local Photography Blog Z6III files are not Nikon HE/HE* JPEG-XS streams.
- Fotopolis `https://files.fotopolis.pl/NIKON_Z6III_RAW.rar` is very slow and about `1.66 GB`; a partial extract showed first file `DSC_0005.NEF` is not Nikon HE/HE* JPEG-XS, so raw.pixls is the cleaner Z6III evidence source.

Current final interpretation:

- The large color/chroma bands were fixed by GTLI fallback coverage.
- The tile-period residual was further reduced by aligning the later-tile memcpy/HH output head phase (`memcpy_start=4`) and saving carry from stripe `28`.
- The remaining detected rows are either top-border rows or isolated high-detail local hits, with no current evidence of full-width color banding in the validated JPEG-XS sample set.

## 2026-06-04 final plus Z6III HE gate

New Z6III/Z63 samples downloaded from raw.pixls using parallel HTTP range requests:

- `out\nhe_color_band_fix_20260604\z6iii_he_sample_search\raw_pixls_z6_3\Nikon_Z6__3_High_Efficiency_FX.NEF`
- `out\nhe_color_band_fix_20260604\z6iii_he_sample_search\raw_pixls_z6_3\Nikon_Z6__3_High_Efficiency_Star_FX.NEF`

Probe artifact:

- `out\nhe_color_band_fix_20260604\z6iii_he_sample_search\raw_pixls_z6_3_precinct_summary.json`

Final seven-sample no-env decode gate:

- `out\nhe_color_band_fix_20260604\parallel_tile_sweep_20260604\final_default_plus_z6iii_he`
- `out\nhe_color_band_fix_20260604\parallel_tile_sweep_20260604\final_default_plus_z6iii_he_metrics.json`
- `out\nhe_color_band_fix_20260604\parallel_tile_sweep_20260604\final_default_plus_z6iii_he_thumb_compare.json`
- `out\nhe_color_band_fix_20260604\parallel_tile_sweep_20260604\final_default_plus_z6iii_he_strict_band_summary.json`

Strict final band summary:

| Sample | Target role | RGB bad/interior rows | Raw local bad interior rows | RGB zero interior rows | Max contiguous zero run |
|---|---|---:|---:|---:|---:|
| `Z8_HE_low.NEF` | Z8 HE | `0/0` | 0 | 0 | 51 px |
| `Z8_HE_high.NEF` | Z8 HE* | `2/2` | 0 | 1 | 28 px |
| `Nikon_Z6__3_High_Efficiency_FX.NEF` | Z6III/Z63 HE | `2/0` | 0 | 0 | 34 px |
| `Nikon_Z6__3_High_Efficiency_Star_FX.NEF` | Z6III/Z63 HE* | `2/0` | 0 | 0 | 12 px |
| `Z6III_HE_Star_FX.NEF` | duplicate Z6III/Z63 HE* regression | `2/0` | 0 | 0 | 12 px |
| `Z9_HE.NEF` | Z9 HE | `1/0` | 0 | 0 | 28 px |
| `Z9_HE_Star.NEF` | extra Z9 HE* regression | `0/0` | 0 | 0 | 12 px |

Interpretation: every required named target now has direct JPEG-XS sample coverage. The only remaining interior RGB threshold hits are the known Z8 HE* local rows around `4126/4134`; raw local banding is clean there and the max contiguous zero run is only 28 pixels out of 8280, so this is not a full-width color-band defect. Z6III/Z63 HE and HE* have zero interior RGB band rows and zero raw local interior band rows.
