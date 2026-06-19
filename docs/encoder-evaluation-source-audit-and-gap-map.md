# Encoder Evaluation Source Audit And Gap Map

Date: 2026-06-02

Purpose: make the literature search and evidence chain auditable. The existing notes already list many papers and standards; this file records what the current corpus proves, what it does not prove, and which new sources were added in the June 2 search pass.

## Research question

How should image encoders be evaluated, and what can be concluded from that framework about the encoder-side mathematics of LibRaw #826 Nikon HE and LibRaw #824 Sony CRAW HQ?

This question has three separable layers:

1. General image codec evaluation: rate, distortion, perception, subjective quality, complexity, reproducibility.
2. RAW/Bayer encoder evaluation: CFA-domain distortion, Bayer phase, highlight/black regions, line/tile latency, rate control, buffer/resource behavior.
3. Nikon HE vs Sony CRAW HQ mathematics: what the inverse decoder structures imply about each encoder family, without claiming a same-source RD winner.

## Corpus-first status

Current local corpus:

- [`codec-paper-evaluation-matrix.md`](codec-paper-evaluation-matrix.md): 50 core entries across standards, learned codecs, IQA metrics, Bayer CFA compression, and the two LibRaw PRs.
- [`codec-evaluation-literature-reading-notes.md`](codec-evaluation-literature-reading-notes.md): extended entries #51-#87, focused on CLIC, CompressAI, RDP tradeoff, learned/generative codecs, Bayer CFA, and subjective evaluation.
- [`encoder-math-only-nikon-he-vs-sony-crawhq.md`](encoder-math-only-nikon-he-vs-sony-crawhq.md): encoder-only mathematical synthesis.
- [`controlled-rate-encoder-benchmark-protocol.md`](controlled-rate-encoder-benchmark-protocol.md): same-source, controlled-rate, RD/BD-rate protocol that states what would be required to name a real encoder winner.
- [`virtual-four-plane-proxy-benchmark.md`](virtual-four-plane-proxy-benchmark.md): first L2 proxy experiment using the same four-plane synthetic RGGB inputs and controlled target bpp.

Coverage is strong for:

- Rate-distortion and BD-rate methodology.
- Full-reference and perceptual quality metrics.
- JPEG XS / JPEG AI / JPEG XL standard-style evaluation.
- Bayer CFA compression and the need for real RAW data.
- Decoder conformance translation for LibRaw PR review.

Coverage is weaker for:

- Public Sony CRAW HQ encoder-side specification.
- Public Nikon HE encoder-side controls beyond JPEG-XS-like/TicoRAW framing.
- Same-source, multi-rate benchmark data for Nikon HE vs Sony CRAW HQ.
- Real encoders or faithful encoder reconstructions that can accept the same linear Bayer arrays at controlled rates.
- Hardware/energy numbers specific to camera RAW encoders.

## Search strategy used on 2026-06-02

Sources searched:

- arXiv API for recent image-compression and theoretical RD work.
- JPEG official pages for standard/CTC evidence.
- CLIC and CompressAI project pages for reproducible benchmark conventions.
- LibRaw PR API / git refs for current #824/#826 status.

Search strings:

```text
all:"learned image compression" AND all:"rate-distortion"
all:"image compression" AND all:"subjective" AND all:"PSNR"
all:"Bayer CFA" AND all:"compression"
all:"JPEG AI" AND all:"image coding"
```

Observed search results:

- arXiv `learned image compression` + `rate-distortion`: 102 records; first page screened.
- arXiv `image compression` + `subjective` + `PSNR`: 4 records; all screened.
- arXiv `Bayer CFA` + `compression`: 0 records; existing Bayer evidence therefore comes from JPEG/PMC/PubMed/older journal sources rather than arXiv.
- arXiv `JPEG AI` + `image coding`: 2 records; one included as a standard overview source.

Inclusion criteria:

- The source must explicitly discuss codec evaluation, rate-distortion, quantization/context/entropy modeling, subjective/perceptual metrics, complexity, or Bayer/RAW compression.
- For the Nikon/Sony comparison, the source must help map an encoder evaluation dimension to one of: transform, quantization, entropy/context model, rate control, error propagation, or complexity.
- Public standard/project pages are allowed when they define benchmark protocols or official evaluation conventions.

Exclusion criteria:

- Sources about image restoration, enhancement, segmentation, or adversarial attacks unless they directly define codec evaluation metrics.
- Sources that only show a new architecture but do not add evaluation-method insight beyond already-covered RD/PSNR/MS-SSIM.
- Sources that require a non-public full text to establish the claim.

## Source quality tiers

These tiers are project-specific. They are not a biomedical evidence hierarchy.

| Tier | Meaning | Examples in corpus | How to use |
|---|---|---|---|
| S1 | Standard / official benchmark / reference implementation | JPEG XS, JPEG AI CTC, JPEG XL/libjxl, CLIC, CompressAI | Strong evidence for what metrics and reporting fields are expected |
| S2 | Peer-reviewed or accepted research paper | JPEG XS Bayer CFA, JPEG AI overview with DOI, Sensors Bayer CFA review | Strong evidence for evaluation patterns and domain constraints |
| S3 | arXiv preprint / project paper | recent LIC and RD theory papers | Useful for current research direction; do not overstate as standard practice |
| S4 | Vendor / PR / implementation evidence | TicoRAW pages, LibRaw PRs, local code | Useful for object-specific facts; not enough for general codec superiority |

## New sources added by this pass

| ID | Source | Tier | Evaluation contribution | Mapping to Nikon/Sony math |
|---|---|---|---|---|
| A1 | Fu et al., [ChWDTA: Channel-wise Wavelet-Domain Transformer Attention and Entropy Modeling for Learned Image Compression](https://arxiv.org/abs/2606.00111), 2026 | S3 | Reports BD-rate reductions on Kodak, CLIC Professional Validation, and Tecnick; combines wavelet-domain representation with entropy modeling and a complexity tradeoff by changing slice count | Reinforces that transform choice and entropy slicing are evaluated together. Useful analogy for Nikon's wavelet/GCLI surface and Sony's hierarchical wavelet/residual surface |
| A2 | Blard et al., [Spatial Competition for Low-Complexity Learned Image Compression](https://arxiv.org/abs/2605.13243), 2026 | S3 | Encoder selects a local codec by rate-distortion cost and reports rate reduction plus MACs/pixel | Supports local-content and complexity-aware evaluation; maps to RAW tile/precinct decisions and the need to report latency/working set |
| A3 | Iliopoulou et al., [ARCHE: Autoregressive Residual Compression with Hyperprior and Excitation](https://arxiv.org/abs/2603.10188), 2026 | S3 | Reports BD-rate versus learned baselines and VVC Intra, plus parameter count and per-image runtime | Strengthens the point that entropy/context modeling and practical runtime must be reported together |
| A4 | Wang et al., [A Theoretical Framework for Rate-Distortion Limits in Learned Image Compression](https://arxiv.org/abs/2601.09254), 2026 | S3 | Decomposes R-D loss into variance estimation, quantization strategy, and context modeling | Directly supports the encoder-only decomposition used for Nikon/Sony: transform/statistics, quantization, and context/entropy are distinct variables |
| A5 | Esenlik et al., [An Overview of the JPEG AI Learning-Based Image Coding Standard](https://arxiv.org/abs/2510.13867), 2025, DOI [10.1109/TCSVT.2025.3613244](https://doi.org/10.1109/TCSVT.2025.3613244) | S2 | Standard overview reports BD-rate reductions across multiple metrics and deployment/interoperability concerns | Supports the framework's insistence on multiple metrics plus standard-style reproducibility, rather than a single PSNR/bpp claim |
| A6 | Yang and Mandt, [Towards Empirical Sandwich Bounds on the Rate-Distortion Function](https://arxiv.org/abs/2111.12166), ICLR 2022 | S2/S3 | Estimates empirical upper/lower bounds for R-D functions and reports remaining room versus practical codecs | Supports the "no winner without same-source RD curve" rule: codec efficiency is a relation to source distribution and distortion criterion |

Sources screened but not added as core evidence:

- `Control Your View: High-Resolution Global Semantic Manipulation in Learned Image Compression` (2026): relevant to robustness of LIC systems, but it is adversarial/downstream-task focused rather than encoder evaluation for RAW.
- `Adaptive Fused Prior Transfer for Controllable Generative Image Compression` (2026): useful for perceptual/generative codecs, but generative-prior evaluation is less relevant to faithful RAW reconstruction.
- `Progressive Learned Image Compression for Machine Perception` (2025): relevant to machine-oriented compression, but the present Nikon/Sony comparison is sensor RAW fidelity, not downstream classifier utility.
- `ConvNeXt-ChARM` (2023): reports BD-rate and subjective analysis, but its methodological contribution overlaps with already-covered LIC sources.

## Evidence-to-claim map

| Claim | Current evidence strength | Evidence | Caveat |
|---|---|---|---|
| Encoder evaluation must be rate-distortion, not file-size-only | Strong | JPEG AI CTC, Bjontegaard, LIC papers, WebP/JPEG XL studies, A4/A6 | Still needs same-source encoder access to apply to Nikon/Sony |
| RAW/Bayer evaluation should be CFA-domain before demosaic | Strong | JPEG XS Bayer CFA, Bayer CFA review, real CFA compression paper | Public Sony/Nikon encoder specs remain absent |
| A fair Nikon/Sony winner test must use same-source controlled-rate inputs | Strong as methodology | `controlled-rate-encoder-benchmark-protocol.md`, JPEG AI CTC, CLIC/CompressAI benchmark conventions, Bjontegaard | The protocol can be specified now, but current public evidence cannot execute the L1 test |
| Complexity and latency are first-class codec metrics | Strong | JPEG XS low-latency framing, CLIC leaderboard fields, ELIC/Contextformer/eContextformer, A2/A3 | Camera hardware power/resource numbers are still missing |
| Nikon HE has a clearer rate-control mathematical surface | Moderate | #826 Bp/Br/GCLI/GTLI code, JPEG-XS-like PR description, TicoRAW/JPEG XS sources | This infers from decoder syntax; actual Nikon encoder policy is not public |
| Sony CRAW HQ is more Bayer-prior-specific | Moderate | #824 LLVC3 green/RB residual reconstruction, final-green clamp, Sony LUT behavior | This infers from decoder math and samples; actual Sony encoder allocation policy is not public |
| Nikon HE is better than Sony CRAW HQ | Not proven | No same-source multi-rate encoder outputs | Must not be claimed |
| Sony CRAW HQ is better than Nikon HE | Not proven | No same-source multi-rate encoder outputs | Must not be claimed |

## Gap map for completion

| Requirement from goal | Current evidence | Status | What would close it further |
|---|---|---|---|
| Systematically collect/read encoder evaluation literature | Matrix + reading notes + this audit; 90+ sources across standards, LIC, IQA, Bayer CFA, PR evidence | Good but still expandable | Add formal PRISMA-style counts and DOI/S2 metadata for core sources |
| Extract encoder evaluation framework | `codec-paper-evaluation-matrix.md`, `codec-evaluation-literature-reading-notes.md`, this audit, and the minimum report-card table in `encoder-math-only-nikon-he-vs-sony-crawhq.md` | Strong | Keep the final report language tied to the report-card axes rather than a single bpp/PSNR number |
| Design a controlled-rate encoder benchmark | `controlled-rate-encoder-benchmark-protocol.md` | Strong as an experimental protocol | Requires same-source Bayer inputs and both encoders, or clearly labeled proxy encoders, to produce results |
| Run a same-source four-plane proxy benchmark | `virtual-four-plane-proxy-benchmark.md`, `tools/proxy_four_plane_benchmark.py`, `tools/compute_bd_rate.py`, and `out/proxy_four_plane_benchmark_v2/*.csv` | Completed for L2 proxy evidence: 24 RGGB inputs, six target bpp points, whole/per-plane/highlight/shadow/detail metrics | Does not prove production Nikon/Sony encoder superiority |
| Compare Nikon HE and Sony CRAW HQ at encoder-math level | `encoder-math-only-nikon-he-vs-sony-crawhq.md`, now with code evidence anchors for the current #824/#826 heads | Strong for inferred mathematical families | Add immutable GitHub permalinks if the comparison is exported outside this local workspace |
| Avoid decoder-side overclaiming | Math-only document states this repeatedly | Strong | Keep final conclusion phrased as "appears mathematically..." not "is better" |
| Determine which is better | Not possible from current public evidence | Incomplete by evidence, not by effort | Requires same-source Bayer input and both encoders, or faithful encoder reconstructions |

## Current safe conclusion

The current evidence supports a ranked-method conclusion, not a winner:

```text
Nikon HE is mathematically easier to evaluate as a standardizable,
rate-controllable transform codec because its public decoder exposes
precincts, bit-plane decisions, GCLI/GTLI, and subband reconstruction.

Sony CRAW HQ is mathematically easier to understand as a camera-specific
Bayer predictor/residual codec because the public reconstruction centers on
green, R/B residuals, final-green clamp order, and a Sony code-to-sample LUT.

Neither conclusion proves rate-distortion superiority. The fair "which is
better" experiment still requires same-source, multi-rate encoder outputs.
```

## Source links checked in this pass

Core links that returned HTTP 200 using `curl.exe -L -I` on 2026-06-02:

- <https://github.com/LibRaw/LibRaw/pull/824>
- <https://github.com/LibRaw/LibRaw/pull/826>
- <https://jpeg.org/jpegxs/documentation.html>
- <https://ds.jpeg.org/documents/jpegxs/wg1n100275-096-COM-JPEG_XS_in-depth_series_raw_image_compression.pdf>
- <https://pubmed.ncbi.nlm.nih.gov/34270422/>
- <https://pmc.ncbi.nlm.nih.gov/articles/PMC9658152/>
- <https://jpeg.org/items/20201028_jpeg_ai_common_test_conditions.html>
- <https://arxiv.org/abs/2304.12852>
- <https://github.com/InterDigitalInc/CompressAI>
- <https://clic2025.compression.cc/leaderboard/image_0_075/test/>

Additional sources added in this pass that returned HTTP 200 using the same command:

- <https://arxiv.org/abs/2606.00111>
- <https://arxiv.org/abs/2605.13243>
- <https://arxiv.org/abs/2603.10188>
- <https://arxiv.org/abs/2601.09254>
- <https://arxiv.org/abs/2510.13867>
- <https://arxiv.org/abs/2111.12166>

The JPEG AI overview DOI, <https://doi.org/10.1109/TCSVT.2025.3613244>, resolved to IEEE Xplore (`https://ieeexplore.ieee.org/document/11175396/`) but returned HTTP 418 to this `HEAD` check. I therefore keep the DOI as bibliographic metadata and use the arXiv page above as the accessible verified source link.
