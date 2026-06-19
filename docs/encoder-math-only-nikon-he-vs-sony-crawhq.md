# Nikon HE vs Sony CRAW HQ: encoder-side mathematical comparison

Date: 2026-06-02

Scope: this note deliberately compares only the encoder-side mathematics that can be inferred from the public LibRaw decoder paths and public codec literature. It does not claim a real rate-distortion winner, because neither PR provides a controllable Nikon HE or Sony CRAW HQ encoder for the same Bayer input.

中文摘要：这份文档只讨论“编码器数学”，不把 LibRaw 解码器是否安全、是否能打开文件、是否与 Adobe/NX/Sony 参考输出一致直接等同于压缩算法优劣。当前能较强成立的判断是：Nikon HE 更像 JPEG XS/TicoRAW 语境下的标准化、可率控 RAW transform codec；Sony CRAW HQ 更像 Sony 固定相机生态中的 green-backbone + R/B residual + LUT 私有管线。前者数学上更容易写成多码率 bit-plane/precinct RD 问题，后者数学上更依赖 Bayer 统计和厂商固定参数。真正回答“谁压得更好”，仍然需要同一个线性 Bayer 数组分别经过两个 encoder 后做多码率 RD/BD-rate、RAW 分相位误差、highlight/black 区域误差、rate-control 和 latency/memory 评价。

Current PR facts verified on 2026-06-02:

- LibRaw #824 is open and mergeable; head is `1fa7855cf2ae13679246870fbd42a2da912785cb`.
- LibRaw #826 is open and mergeable; head is `8aebd05d0f0378dcd5df2bc4cefb0f7104aa6ee3`.

Related local notes:

- Paper/standard evidence matrix: [`codec-paper-evaluation-matrix.md`](codec-paper-evaluation-matrix.md)
- Extended literature notes: [`codec-evaluation-literature-reading-notes.md`](codec-evaluation-literature-reading-notes.md)
- Source audit and gap map: [`encoder-evaluation-source-audit-and-gap-map.md`](encoder-evaluation-source-audit-and-gap-map.md)
- Controlled-rate encoder benchmark protocol: [`controlled-rate-encoder-benchmark-protocol.md`](controlled-rate-encoder-benchmark-protocol.md)
- L2 virtual four-plane proxy benchmark: [`virtual-four-plane-proxy-benchmark.md`](virtual-four-plane-proxy-benchmark.md)
- PR operation playbook: [`libraw-824-826-evaluation-playbook.md`](libraw-824-826-evaluation-playbook.md)
- Nikon HE static safety review: [`libraw-826-static-safety-review.md`](libraw-826-static-safety-review.md)

## Why this document exists

The existing playbook is mostly a merge-readiness and decoder-conformance document. The active research goal is narrower and harder: compare Nikon HE and Sony CRAW HQ at the level of encoder mathematics.

That means the right object is not:

```text
camera file -> LibRaw decoder -> visible preview
```

but:

```text
linear Bayer array X
  -> encoder analysis transform T
  -> quantization / bit-plane decision Q
  -> entropy syntax H
  -> bitstream B
  -> inverse decoder D
  -> reconstructed Bayer array X_hat
```

The fair question is:

```text
For the same X and comparable rate R(B), which encoder family gives a better
distortion / latency / memory / rate-control frontier?
```

At present this question is not fully measurable, because the public evidence gives decoders and real camera bitstreams, not both encoders. Still, the decoder code exposes enough inverse structure to compare the mathematical families and derive what a real benchmark must prove.

## Literature-derived encoder evaluation framework

Across JPEG XS, JPEG XL, JPEG AI CTC, learned image compression, Bayer CFA compression, and subjective-quality standards, encoder evaluation repeatedly decomposes into these variables:

| Axis | Mathematical object | Usual evidence | RAW-specific translation |
|---|---|---|---|
| Rate | `R = |B| / (H W)` or bpp against a fixed source | compressed bytes, target bpp, RD curve, BD-rate | strip bpp and ratio vs 14-bit packed are descriptive; only same-source multi-rate encoding proves coding efficiency |
| Distortion | `D(X, X_hat)`, usually MSE/MAE/max error plus perceptual variants | PSNR, MS-SSIM, LPIPS, DISTS, VMAF, MOS | CFA-domain R/G0/G1/B error, highlight subset, black/white-level-aware error, then optional demosaic metrics |
| Perception | human or learned perceptual utility | BT.500/P.910/CLIC-style subjective tests | relevant only for "visually lossless" claims, not for decoder exactness |
| Control | mapping from target rate to actual rate and distortion | target bitrate error, variable-rate curves, buffer fullness | central for in-camera RAW, because storage and burst pipelines need predictable rate |
| Complexity | encoder/decoder resource and latency | CPU/GPU time, FPGA/ASIC resources, memory, decoder size | camera RAW strongly cares about line/tile latency, working buffers, power, and one-pass operation |
| Robustness/reproducibility | syntax conformance and command reproducibility | reference software, illegal stream handling, datasets, scripts | sample matrix, reference decoder version, crop alignment, JSON/CSV diff |

For this project the essential distinction is:

```text
Decoder-side evidence proves D(B) is implemented correctly.
Encoder-side evidence requires comparing E_Nikon(X; r) and E_Sony(X; r)
on the same X across multiple target rates r.
```

The resulting minimum report card is:

| Report item | What must be reported | Why it matters for encoder evaluation | Status for #824/#826 today |
|---|---|---|---|
| Source corpus | real linear Bayer arrays, scene classes, ISO/noise, highlight/black stress crops, CFA phase | codec efficiency is source-distribution dependent | partial real camera bitstreams; no same-source Nikon/Sony encoder corpus |
| Rate axis | actual bpp, target bpp if available, RD points, BD-rate when multiple rates exist | file size alone is not a coding-efficiency proof | descriptive strip bpp exists; controllable multi-rate encoder outputs do not |
| RAW distortion | CFA-domain MAE/RMSE/PSNR/max error by R/G0/G1/B plus highlight and black subsets | RAW codecs must preserve sensor-domain data before demosaic | can be measured for decoder/reference outputs; cannot rank encoders without original same-source input |
| Perceptual utility | optional demosaic/render metrics and subjective tests for visually lossless claims | useful for marketing/user-visible claims, secondary for RAW decoder merge | not a merge gate; should not replace reference conformance |
| Rate control | target-to-actual rate error, local allocation behavior, buffer behavior | in-camera RAW needs predictable storage and burst behavior | Nikon exposes Bp/Br/GCLI/GTLI syntax; Sony public decoder exposes no comparable target-rate knob |
| Complexity | encode/decode time, memory, tile/line latency, one-pass feasibility, cancellation | RAW codecs live inside constrained camera and desktop pipelines | decoder memory/latency can be audited; encoder complexity remains mostly private |
| Robustness | malformed stream behavior, illegal variant handling, truncation, tile/crop edges | codec implementations must fail deterministically | #824 currently has stronger explicit guards; #826 still needs safety cleanup |
| Reproducibility | PR head, reference decoder version, commands, hashes, crop coordinates, JSON/CSV outputs | makes the comparison repeatable instead of anecdotal | local notes now record heads, links, samples, and command-level evidence gaps |

## Common mathematical skeleton

Both codecs can be described as integer transform codecs over a Bayer mosaic:

```text
X in Z^{H x W}                         input linear Bayer mosaic
P_k X                                  tiling / precinct partition
Y_k = T_k(P_k X)                       analysis transform coefficients
Z_k = Q_k(Y_k; lambda, side_state)     quantized or bit-plane-truncated coefficients
B_k = H_k(Z_k, side_info)              entropy-coded syntax
X_hat = D({B_k})                       reconstructed Bayer mosaic
```

The public decoders expose the inverse:

```text
D = C_out o L^{-1} o Q^{-1} o H^{-1}
```

where `C_out` is the final Bayer/color/tone mapping. The encoder comparison must therefore infer:

- what `T` is,
- what degrees of freedom `Q` has,
- how `H` predicts or codes coefficient significance,
- how local errors propagate through `L^{-1}` and final LUT/tone curves.

## Code evidence anchors

The following anchors use the PR heads verified above. Line numbers are evidence for the current checked revisions, not a promise that future PR updates keep the same layout.

| Mathematical claim | Nikon HE #826 code anchor | Sony CRAW HQ #824 code anchor | What the anchor supports |
|---|---|---|---|
| Container partitioning is local/tiled | `src/decoders/nikon_he/nikon_he_decode.cpp:39-190` walks a precinct stream, assigns roughly `n_tiles * 16 + 2` file precincts, decodes 18 precincts per tile, then runs step1/step2 over tile stripes | `src/decoders/sony_arw6.cpp:438-560` parses `A000`/`0000` LLVC3 stream headers and single or tiled stream directories | both codecs are local RAW stream codecs, not whole-frame RGB codecs |
| Nikon exposes explicit rate-control-like syntax | `src/decoders/nikon_he/nikon_he_precinct_header.cpp:41-110` parses `Bp`, `Br`, `Dpb`, and per-LB sig/GCLI/data/sign substreams | no equivalent public Sony target-rate syntax is exposed; `src/decoders/sony_arw6.cpp:926-963` decodes private packet arrays and selectors | Nikon is easier to describe as a controllable bit-plane/precinct codec; Sony remains more fixed-mode from public evidence |
| Nikon coefficient precision is bit-plane coded | `src/decoders/nikon_he/nikon_he_gcli_decode.cpp:39-94` reconstructs GCLI values from significance and unary refinement; `nikon_he_coefficient_decode.cpp:27-78` reconstructs magnitudes bit-plane by bit-plane and then applies sign bits | `src/decoders/sony_arw6.cpp:633-766` decodes private native bit packets, width state, zero runs, coefficient groups, and signs | both have entropy-coded coefficients, but Nikon's bit-plane structure is more directly visible |
| Nikon quantization is GTLI/GCLI-driven | `src/decoders/nikon_he/nikon_he_dequantize.cpp:27-83` uses `gcli - gtli`, midpoint scaling, and implicit zero low bits | Sony public code does not expose a named quantizer; packet selectors and residual values imply a private fixed policy | Nikon's inverse quantization maps cleanly to RD literature variables; Sony needs encoder reconstruction to expose `Q` |
| Both use integer wavelet-family inverse transforms | `src/decoders/nikon_he/nikon_he_tile.h:18-33`, `nikon_he_tile.cpp:149-224`, and `nikon_he_bayer.h:28-103` describe horizontal/vertical IDWT, 18 precincts, 32 stripes, and 4-plane to Bayer reconstruction | `src/decoders/sony_arw6.cpp:998-1045` implements inverse 5/3 axes; `1117-1142` synthesizes one LLVC3 level | both belong to the integer transform/residual family |
| Final Bayer reconstruction differs substantially | `src/decoders/nikon_he/nikon_he_bayer.cpp:124-199` performs final inverse plus tone-LUT lookup into 14-bit Bayer output | `src/decoders/sony_arw6.cpp:1292-1369` reconstructs final green and maps signed internal codes through a 4096-entry LUT; `1530-1619` forms R/B from clamped green average plus residuals | Nikon's output stage is tone-LUT after transform reconstruction; Sony's output equation is green/backbone plus R/B residual and LUT |
| Entropy context differs | `src/decoders/nikon_he/nikon_he_predict_lut.h:18-56` and `nikon_he_predecessor.h:18-123` describe GCLI prediction, previous-band state, and precinct-16 resets | `src/decoders/sony_arw6.cpp:655-766` shows width-state, zero-run, group, and sign decoding, but without public standard semantics | Nikon context modeling is easier to audit as named GCLI/GTLI state; Sony context is private and inferred |

## Nikon HE inferred encoder model

Public #826 code and PR text describe Nikon HE as JPEG-XS-like. The decoder path at `8aebd05d` has these mathematical components:

```text
B_N
  -> strip prefix skip + precinct stream
  -> per-precinct header: Bp, Br, Dpb
  -> GCLI / GTLI significance reconstruction
  -> coefficient magnitude + sign bit-plane reconstruction
  -> dequantization
  -> horizontal IDWT + vertical IDWT
  -> step1 4-plane to 2-plane Bayer merge
  -> step2 tone-curve LUT to 14-bit RGGB output
```

The corresponding encoder-side abstraction is:

```text
E_N(X; r):
  1. Partition X into vertical tiles / 64-row regions and 18-precinct tile windows.
  2. Apply an integer 2-D 5/3-like analysis transform to obtain subbands.
  3. For each precinct/subband, choose GTLI/Bp/Br-like bit-plane truncation.
  4. Encode GCLI values with predecessor state and prediction.
  5. Encode coefficient magnitudes by retained bit-planes plus sign bits.
  6. Emit a JPEG-XS-like precinct stream with overlap/tail syntax.
```

Key mathematical features:

- **Explicit bit-plane control.** `Bp`, `Br`, GTLI, GCLI, and coefficient bit-plane coding expose a natural rate-control handle. This is the strongest mathematical reason Nikon HE looks closer to a standard codec family.
- **Subband-aware allocation.** A bit-plane syntax can allocate different precision to LL, HL, LH, HH and later Bayer/tone stages. That is exactly the shape expected by JPEG XS-like RAW coding literature.
- **Predictive entropy state.** GCLI predecessor state makes rate depend on local subband continuity. It can improve coding efficiency, but it creates state correctness and reset-boundary requirements.
- **Low-latency potential.** Precinct/tile organization is compatible with line/stripe latency rather than full-frame transform latency. Whether #826's implementation realizes that efficiently is an implementation question; the codec family itself has this mathematical affordance.

Error propagation can be written as:

```text
Y_hat = Y + e_Q
X_hat = C_N( T_N^{-1}(Y_hat) )
Delta X ~= J_{C_N} T_N^{-1} e_Q
```

Here `J_{C_N}` is the local slope of the tone LUT. Therefore Nikon HE's visible or numeric error is not just the quantizer error; it is quantizer error after inverse lifting and local tone-curve expansion.

## Sony CRAW HQ inferred encoder model

The #824 Sony path is a private LLVC3 reconstruction inferred from real samples, Python traces, Sony/Adobe comparison, and the LibRaw decoder. The public local decoder exposes these inverse components:

```text
B_S
  -> stream directory or single A000 stream
  -> packet groups and residual arrays
  -> hierarchical 5/3-like inverse synthesis for green and R/B residual planes
  -> guarded-height synthesis for non-16-aligned R6 tile heights
  -> final green reconstruction
  -> R/B = average(clamp12(final_green_pair)) + residual
  -> 12-bit internal code to Sony sample LUT
  -> RGGB output
```

The corresponding encoder-side abstraction is:

```text
E_S(X; camera_mode):
  1. Partition X into one LLVC3 stream or a stream directory of tiles.
  2. Convert Bayer data into a green backbone plus R/B residual representation.
  3. Apply hierarchical integer 5/3-like analysis over several groups.
  4. Packetize group/component arrays with private control fields and records.
  5. Use a fixed or camera-mode-dependent quantization / residual coding policy.
  6. Encode a final green detail layer and residual relation.
```

Key mathematical features:

- **Green-centered representation.** Sony's model treats green as the backbone and codes red/blue relative to green. This is a strong Bayer-specific prior, because green samples dominate luminance structure and are denser in RGGB.
- **Residual rather than generic subband syntax.** R/B error is controlled through residual planes tied to final green. This may be very efficient for natural Bayer statistics, but the public evidence does not expose an explicit variable-rate control surface comparable to Nikon's Bp/GTLI/GCLI syntax.
- **Static nonlinear output LUT.** Sony maps a 12-bit internal code domain to output samples with a LUT. A one-code error becomes a local sample error equal to a local LUT step.
- **Clamp order is mathematically part of the codec.** The Adobe/Sony comparison showed that high-light `final_green` can overshoot internally; R/B prediction must average `clamp12(final_green_pair)`, not the unclamped pair. That is an encoder/decoder model equation, not a cosmetic implementation detail.

Sony error propagation is better written in two stages:

```text
G_f = F_G^{-1}(B_S)
R_res, B_res = F_C^{-1}(B_S)
G_pred = avg(clamp12(G_f_even), clamp12(G_f_odd))
R_code = G_pred + 2 R_res
B_code = G_pred + 2 B_res
X_hat = LUT_S(clamp12(code + bias))
```

For an internal code error `delta c`, the sample-domain error is approximately:

```text
delta x ~= s_S(c) * delta c
```

where `s_S(c) = LUT_S(c+1) - LUT_S(c)` is the local LUT step. This explains why a one-code residual or predictor error is not uniform across the tone range, and why high-light code-domain checks are necessary.

## Direct mathematical comparison

| Dimension | Nikon HE inferred encoder | Sony CRAW HQ inferred encoder | Encoder-side consequence |
|---|---|---|---|
| Source model | General Bayer RAW transformed into JPEG-XS-like subbands | Bayer-specific green backbone plus R/B residuals | Nikon is more standard-transform-like; Sony is more camera/Bayer-prior-specific |
| Transform | Explicit 2-D 5/3 wavelet / IDWT pipeline with precincts and subbands | Hierarchical 5/3-like synthesis plus special final-green stage | Both are wavelet-family codecs; Sony's final color relation is more specialized |
| Quantization control | Bp/Br/GTLI/GCLI imply bit-plane truncation and subband-level precision control | Public decoder exposes packets/residuals but not a clean target-rate parameter | Nikon likely has clearer rate-control mathematics; Sony may be tuned by fixed camera modes |
| Entropy model | GCLI prediction, predecessor state, coefficient magnitude/sign bit-planes | Private packet records and residual coding; entropy syntax less interpretable from public evidence | Nikon is easier to benchmark as a codec; Sony is easier to validate by reference output than to vary as an encoder |
| Error domain | Subband coefficient error through inverse 5/3 and tone LUT | Green-predictor/residual code error through clamp and Sony LUT | Nikon error is subband-spread; Sony error is predictor/LUT-local and can be channel-specific |
| Bayer specificity | Bayer reconstruction is late-stage step1/step2 | Bayer structure is central: green first, R/B residuals | Sony likely exploits RGGB statistics more directly; Nikon likely gains from standardized transform syntax |
| Low-latency form | Precinct/tile structure is naturally low-latency | Tile/stream structure is local but private | Both can be low-latency; Nikon's form is easier to compare to JPEG XS literature |
| Reproducible evaluation | Needs HE encoder or reference implementation with controllable rates | Needs Sony encoder or faithful encoder reconstruction | Current public PRs cannot prove RD superiority for either side |

## What can be concluded now

Strong conclusions:

1. Both codecs are transform/residual RAW codecs over Bayer data, not simple packed RAW or post-demosaic image codecs.
2. Nikon HE has a more explicit standard-codec mathematical surface: precincts, bit-plane decisions, GCLI/GTLI, dequantization, IDWT, Bayer/tone reconstruction.
3. Sony CRAW HQ has a more Bayer-specialized mathematical surface: green backbone, R/B residuals, final-green predictor, clamp order, and static code-to-sample LUT.
4. Nikon HE is mathematically easier to place into a JPEG XS/TicoRAW-style rate-control and low-latency framework.
5. Sony CRAW HQ is mathematically easier to describe as a fixed camera-mode reconstruction pipeline; its real strength or weakness depends on how well the private encoder chooses packets/residual precision for actual Sony sensor statistics.

Weak or unproven conclusions:

1. "Nikon HE compresses better than Sony CRAW HQ" is unproven.
2. "Sony CRAW HQ preserves more highlight information" is unproven.
3. Single-file compression ratios are not encoder superiority evidence, because scene, sensor, exposure, crop, noise, and camera-mode entropy differ.
4. Decoder exactness does not imply encoder optimality. It only proves that `D_N(B_N)` or `D_S(B_S)` is faithfully implemented.

## A fair same-source mathematical benchmark

To decide which encoder is better, the experiment must be:

```text
Given a set of linear Bayer arrays X_i:
  for each target rate r_j:
    B_Nij = E_N(X_i; r_j)
    B_Sij = E_S(X_i; r_j)
    X_Nij = D_N(B_Nij)
    X_Sij = D_S(B_Sij)
    measure R, D_raw, D_highlight, D_by_site, latency, memory
```

Required datasets:

- Real camera Bayer arrays, not RGB images converted into fake CFA.
- A mix of low noise, high ISO noise, saturated highlights, fine texture, smooth gradients, dark regions, and tile-boundary stress scenes.
- If possible, same sensor input. If not possible, the result must be labeled decoder/sample conformance, not encoder RD comparison.

Required metrics:

| Metric | Formula / report | Reason |
|---|---|---|
| bpp | `8 |B| / (H W)` | primary rate axis |
| raw MSE/PSNR | per CFA plane and whole mosaic | numeric fidelity |
| max error | `||X - X_hat||_infty` | catches near-lossless bound violations |
| highlight error | same metrics for `X >= threshold` | catches LUT/tone/predictor amplification |
| black-region error | same metrics near black level | catches noise-floor and bias behavior |
| site split | R, G0, G1, B | Bayer-specific correctness |
| local RD outliers | per image / per crop | avoids average hiding bad content |
| rate-control error | `|target_bpp - actual_bpp|` | in-camera encoder practicality |
| latency / working set | lines, tiles, memory, time | RAW burst/video-like practicality |
| BD-rate | only after at least four comparable rate points | standard codec summary, but invalid for single-rate claims |

For perceptual metrics:

- Use demosaic-preview PSNR/MS-SSIM/LPIPS/DISTS only as a secondary view.
- Keep RAW-domain metrics primary, because these codecs operate before demosaic and before final camera rendering.

## Expected comparative hypotheses

These are hypotheses, not established results:

1. Nikon HE should have stronger controllable RD behavior if its encoder exposes and optimizes Bp/Br/GTLI across precincts.
2. Sony CRAW HQ may be highly efficient for Sony sensor statistics because its representation is tied directly to green/RB residual structure and a camera-specific LUT.
3. Nikon HE may be easier to generalize across bodies because the syntax resembles a known JPEG XS/TicoRAW family.
4. Sony CRAW HQ may be harder to generalize but easier to make compact in a fixed camera ecosystem.
5. At high compression, Nikon's subband bit-plane truncation likely produces wavelet/subband errors; Sony's errors likely show as predictor/LUT/local channel errors, especially in highlights.

## How to phrase the "which is better" answer today

The most rigorous current answer is:

```text
Nikon HE appears mathematically stronger as a standardizable, rate-controllable
RAW codec family. Sony CRAW HQ appears mathematically stronger as a specialized
green/residual pipeline tuned to Sony Bayer statistics. But public #824/#826
evidence proves decoder structure and conformance, not encoder RD dominance.
The winner cannot be named without same-source, multi-rate encoder outputs.
```

This is less dramatic than a single winner, but it is the only conclusion that follows from the current evidence without smuggling decoder success into encoder-quality claims.

## Source anchors

- LibRaw #824 Sony ARW6 CRAW HQ PR: <https://github.com/LibRaw/LibRaw/pull/824>
- LibRaw #826 Nikon HE PR: <https://github.com/LibRaw/LibRaw/pull/826>
- JPEG XS documentation and publications: <https://jpeg.org/jpegxs/documentation.html>
- JPEG XS RAW compression in-depth PDF: <https://ds.jpeg.org/documents/jpegxs/wg1n100275-096-COM-JPEG_XS_in-depth_series_raw_image_compression.pdf>
- Bayer CFA Pattern Compression With JPEG XS: <https://pubmed.ncbi.nlm.nih.gov/34270422/>
- Compression for Bayer CFA Images review: <https://pmc.ncbi.nlm.nih.gov/articles/PMC9658152/>
- JPEG AI Common Test Conditions: <https://jpeg.org/items/20201028_jpeg_ai_common_test_conditions.html>
- Bjontegaard methodology warning: <https://arxiv.org/abs/2304.12852>
- CompressAI reproducible evaluation toolkit: <https://github.com/InterDigitalInc/CompressAI>
- CLIC 2025 leaderboard fields including bitrate, PSNR, MS-SSIM, decoder size, and decoding time: <https://clic2025.compression.cc/leaderboard/image_0_075/test/>
