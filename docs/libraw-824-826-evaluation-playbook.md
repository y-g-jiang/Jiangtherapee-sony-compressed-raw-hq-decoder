# LibRaw #824/#826 RAW 压缩解码评价 Playbook

日期：2026-06-01  
对象：LibRaw #824 Sony ARW6 CRAW HQ 与 LibRaw #826 Nikon HE。  
目的：把图像编码器论文里的评价方法落到两个实际 PR：一个是当前本地 Sony CRAW HQ decoder，一个是 Nikon HE decoder PR。

## 当前证据快照

| 项 | #824 Sony ARW6 CRAW HQ | #826 Nikon HE |
|---|---|---|
| PR | <https://github.com/LibRaw/LibRaw/pull/824> | <https://github.com/LibRaw/LibRaw/pull/826> |
| 状态 | open | open |
| head | `1fa7855cf2ae13679246870fbd42a2da912785cb` | `8aebd05d0f0378dcd5df2bc4cefb0f7104aa6ee3` |
| diff size | 4 commits, 1685 additions, 11 deletions, 14 files | 3 commits, 4307 additions, 83 deletions, 42 files |
| 本地证据 | `C:\Users\姜尧耕\Downloads\LibRaw-pr-sony-arw6-craw-hq` clean at `1fa7855c` | temporary clone at `%TEMP%\libraw_pr826_nikon_he`, clean at `8aebd05` |
| 已有样本证据 | A7M5 single-stream, A7R6 2x2 tiled guarded-height | Z8/Zf `Bp=4` HE path samples, Z9/Z8/Zf HE* or negative samples |
| 最大缺口 | `dng_levels` review comment 仍需清理或解释；正式 LibRaw TIFF vs Python/Adobe/Sony JSON 表还应固化 | 还缺 #824 级别的 NX/Adobe/reference common-crop pixel diff 和 safety/static review 表 |

## 从论文评价方法到 RAW decoder 证据

| 论文/标准评价维度 | 普通 image encoder 怎么证明 | 对 #824/#826 的翻译 |
|---|---|---|
| Conformance | 标准 bitstream、reference software、非法码流处理 | 相机真实 ARW/NEF、Adobe/Sony/Nikon/reference output、unsupported HE* 明确失败 |
| Rate | bpp、compression ratio、RD curve、BD-rate | strip bpp、ratio vs 14-bit packed，多机型/多模式样本表 |
| Distortion | MSE、PSNR、max error、histogram | CFA-domain exact/nonzero/max_abs/RMSE/histogram，按 R/G0/G1/B 分相位 |
| Perception | SSIM/MS-SSIM/LPIPS/DISTS/Butteraugli | 只用于 preview 辅助；不能替代 reference exactness |
| Subjective | MOS、pairwise、BT.500/P.910/CLIC | 只用于厂商 visually lossless 声明，不是 LibRaw decoder merge gate |
| Complexity | speed、latency、decoder size、memory | R6/M5/Z8/Zf wall time、memory cap、one-tile/one-stream decode |
| Robustness | malformed stream、packet loss、random access | strip/packet/precinct length guard、tile bounds、overflow、cancel、bad HE*/bad LLVC3 |
| Reproducibility | datasets、anchors、commands、versions | 样本路径、reference decoder version、crop/black/CFA phase、JSON/CSV 输出 |

## 压缩结构并排理解

| 维度 | Nikon HE in #826 | Sony CRAW HQ in #824 |
|---|---|---|
| 公开生态 | PR 自述为 JPEG-XS-like 2D 5/3 wavelet；讨论中提到 TicoRAW/LibRaw private decoder | Sony 私有 LLVC3，没有公开完整标准；证据来自逆向、Imaging Edge/Adobe 对比、真实样本 |
| TIFF dispatch | `Compression=34713`，Nikon Z9/Z8/Zf/Z6III，strip 起始 `ff 10 ff 50` SOC/CAP marker | `Compression=32766`，Sony non-DNG CFA，samples=1，12/14 bps，dispatch 到 `sony_arw6_load_raw()` |
| Container entry | `nikon_he_load_raw()` 跳过 strip 前 `0x9b` 字节读 precinct stream | `sony_arw6_find_streams()` 支持 preamble directory、多 stream tiled 与 single stream fallback |
| Spatial transform | horizontal + vertical integer 5/3 DWT，per tile 18 precinct，32 stripes，2-row overflow carry | 分层 5/3-like inverse，group 1/2/3 guarded synthesis，R6 tile height 3336 走 padded/guard path |
| Entropy/context | GCLI/coefficient/sign decode，precinct predecessor state，precinct 16 reset | group/component packet decode，native bits reader，residual predictor |
| Color reconstruction | step1/step2 Bayer reconstruction，piecewise tone curve LUT | green backbone + R/B residual，相对 final green predictor，12-bit code to Sony LUT |
| 高光关键点 | tone LUT/reference output 尚需验证 | final green predictor 必须先 clamp 到 12-bit code domain，否则 R/B 高光泄漏 1-3 LUT code steps |
| 变体 | HE `Bp in {4,5}` 支持，HE* `Bp in {1,2,3}` 当前 unsupported | `A000` single stream，`0000` tiled stream，sequence/version `0x01000000` 等 |
| 当前证据强度 | 结构代码完整，但公开 reference compare 不足 | 已有 Adobe DNG Converter 18.3.1 common crop/highlight diff 讨论与本地 Python guarded reference |

## 代码审查映射

| 检查点 | #824 当前证据 | #826 当前证据 | 结论 |
|---|---|---|---|
| Dispatch guard | `src/metadata/tiff.cpp:2076-2095` 检查 Sony、non-DNG、CFA、samples=1、12/14 bps、raw dimensions | `src/metadata/tiff.cpp:2262-2275` 检查 Nikon 机型与 strip marker | 两者都有 dispatch guard；#824 还需处理 `dng_levels` 可读性 review |
| Stream/precinct size | #824 对 `data_size` 有 1GB 上限、memory cap、stream length、packet rows 检查 | #826 检查 `data_size > 0x9b`，然后整段读入 `std::vector<uint8_t>`；precinct walk 碰到异常 size 时 break | #826 需要更正式的 corrupt/truncated stream 行为审查 |
| Tile/stream bounds | #824 验证 tile x/y/w/h 覆盖并不越过 raw image | #826 按 `n_tiles=(height+63)/64`、`n_file_precincts=n_tiles*16+2` 收集 precinct | #824 的 bounds gate 更显式；#826 需要证明 partial tile/DX crop/tail sentinel 都正确 |
| Memory model | #824 估计 `raw_bytes + data_size + max_tile_pixels * working_per_pixel` 并受 `max_raw_memory_mb` 限制 | #826 分配 full precinct stream、full `bayer`、`tile_coeff_buf`、`step1_scratch`、`overflow`，当前未见 `max_raw_memory_mb` gate | #826 需要 memory/overflow review 表 |
| Cancel behavior | #824 stream loop 和 row loop 中有 `checkCancel()` | #826 静态检索未见 decoder pipeline 内 `checkCancel()` | #826 需要补 cancel 或解释 |
| Unsupported variant | #824 当前没有 Sony CRAW HQ unsupported variant 的同类问题，但 bad stream 应 throw | #826 first precinct `Bp != 4 && != 5` 直接 `LIBRAW_EXCEPTION_UNSUPPORTED_FORMAT` | #826 HE* 行为需要用样本确认不能输出 partial bad image |
| Decode failure semantics | #824 多数 bad condition throw `LIBRAW_EXCEPTION_IO_CORRUPT/ALLOC/EOF` | #826 `decode_nikon_he_image()` 失败后 `memset(raw_image,0)`、`maximum=16383` 并 return | #826 失败语义应和 LibRaw 惯例对齐，建议作为 review finding |
| Reference output | #824 有 Adobe common crop/highlight diff 讨论，且 Python guarded reference 可生成 | #826 PR 自述 end-to-end NEF tested，但缺公开 reference diff | #826 的最大实证缺口是 reference-output compare |

详细的 #826 allocation/overflow/bounds/cancel 静态审查表见 [`libraw-826-static-safety-review.md`](libraw-826-static-safety-review.md)。这份表已经把 `nikon_he_decoder.cpp`、`nikon_he_decode.cpp`、`nikon_he_tile.cpp`、`nikon_he_precinct_decode.cpp`、`nikon_he_bit_reader.cpp`、`nikon_he_predict_lut.h` 和 `nikon_he_predecessor.cpp` 逐项映射到内存、越界、错误流语义和取消检查。

## 当前样本测得的 rate/structure

### Sony #824

| 样本 | 类型 | 结构验证 | strip bpp | ratio vs 14-bit packed | 备注 |
|---|---|---|---:|---:|---|
| `ATR00049.ARW` | A7R6 2x2 tiled guarded-height | `all_packet_validations_pass=true` | 5.949 | 2.35 | 4 streams, tile `5008x3336`, guarded height |
| `DSC00157_1.ARW` | A7M5 single stream low-entropy regression | `all_packet_validations_pass=true` | 1.992 | 7.03 | `A000` single stream |

### Nikon #826

| 样本 | 机型 | RAW size | strip bytes | first Bp | 推断路径 | strip bpp | ratio vs 14-bit packed |
|---|---|---:|---:|---:|---|---:|---:|
| `Nikon_Z8_HE_low.NEF` | NIKON Z 8 | 8280x5520 | 17139200 | 4 | HE supported path | 3.000 | 4.67 |
| `Nikon_Zf_DSC_0043.NEF` | NIKON Z f | 6064x4040 | 9186816 | 4 | HE supported path | 3.000 | 4.67 |

解释：这些 rate 数字只能说明样本压缩率和分流路径，不能证明 decoder 正确。论文里 RD 曲线需要同一 encoder 的多 bitrate 点；这里没有相机 encoder 控制权，所以更应重视 reference-output conformance。

## 最小可接受 PR 评论格式

### #824 Sony CRAW HQ

```text
Samples:
- ATR00049.ARW, ILCE-7RM6, 10016x6672, 4 tiled LLVC3 streams, tile 5008x3336, guarded height
- DSC00157_1.ARW, ILCE-7M5, 7040x4688, single A000 stream

Reference:
- Python guarded decoder from this repo, no tile_edge_mitigation, no native_edge_oracle
- Adobe DNG Converter 18.3.1 / Sony Imaging Edge where available

Metrics:
- probe: all_packet_validations_pass=true
- common crop: exact/nonzero/max_abs/mean_abs/RMSE
- by CFA site: R/G0/G1/B/G
- highlight subset: threshold >=14000
- LUT-code-domain diff with sony_llvc3_static_lut4096_padded_u16.bin
- wall time and memory estimate

Open:
- remove or justify non-DNG dng_levels assignment in tiff.cpp
```

### #826 Nikon HE

```text
Samples:
- HE success path: at least Z8 and Zf first Bp=4, plus Z9/DX crop if available
- HE* unsupported path: first Bp=1/2/3 should throw LIBRAW_EXCEPTION_UNSUPPORTED_FORMAT
- negative path: Compression=34713 but no JPEG XS SOC/CAP marker should not enter HE decoder

Reference:
- Nikon NX Studio / Adobe DNG Converter / LibRaw private decoder if available

Metrics:
- container: Compression=34713, strip marker, strip + 0x9b, first Bp/Br
- common crop pixel diff: overall/by-site/highlight/tone-LUT related regions
- failure semantics: no partial bad raw output
- static safety: precinct size, 6-byte padding, tile tail, allocation, overflow, checkCancel
- wall time and memory

Open:
- current PR only supports HE; HE* groundwork exists but must remain refused
- decode failure currently memset+return rather than throw
- no public reference-output diff yet
```

## Next actions

1. For #824, remove or justify `tiff_ifd[raw].dng_levels` writes in non-DNG Sony dispatch, then rerun R6/M5 LibRaw output and compare against Python guarded reference.
2. For #826, convert `probe_nikon_he_nef.py` outputs into a formal JSON/CSV sample matrix, then add NX/Adobe converted reference outputs when available.
3. For #826, turn the static review findings in [`libraw-826-static-safety-review.md`](libraw-826-static-safety-review.md) into either a PR comment or a small patch proposal: hard-fail truncated precincts, bound GCLI LUT lookup, add `max_raw_memory_mb`, add RAII/destructor for predecessor state, and add `checkCancel()`.
4. For both PRs, keep the review language close to codec-paper evidence: sample list, anchor/reference version, crop alignment, exact error stats, channel/highlight/tile-boundary analysis, runtime/memory, unsupported variants.
