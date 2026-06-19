# RAW 解码器评价样本矩阵

日期：2026-06-04  
目的：把论文/标准里的评价方法落到 LibRaw #824 Sony ARW6 CRAW HQ 与 #826 Nikon HE 的实际样本、参考输出和统计命令上。

并排评价 playbook：[`libraw-824-826-evaluation-playbook.md`](libraw-824-826-evaluation-playbook.md)。

## 2026-06-04 production-fit 实测批次

公开 raw.pixls.us 样张已下载到 `samples/raw_pixls`，本地 Downloads 里的 7 个 Z7 II NEF 作为负样本加入路由检查。批量探测使用并行入口：

```powershell
C:\Python314\python.exe tools\probe_production_fit_samples.py --extra-nef-dir C:\Users\bcm18\Downloads --jobs 15
C:\Python314\python.exe tools\summarize_production_fit_samples.py
C:\Python314\python.exe tools\audit_production_fit_policy.py
C:\Python314\python.exe tools\stream_fitted_sony_selector_eval.py --jobs 15
```

核心输出：

- `out/production_fit_samples/parallel_probe_manifest.json`
- `out/production_fit_samples/real_bitstream_controls.csv`
- `out/production_fit_samples/production_fit_summary.json`
- `out/production_fit_samples/production_fit_policy_audit.csv`
- `out/sony_stream_fitted_selector_eval_20260604/rate_summary.csv`

| 样张或类别 | 来源 | RAW 尺寸 | strip bpp | 关键控制量 | production-fit 解释 |
|---|---|---:|---:|---|---|
| `Sony_ILCE-7M5_full_compressed_HQ.ARW` | raw.pixls.us Sony ILCE-7M5 | 7040x4688 | 4.468011 | 13 packets, type `{"1":4,"3":9}`, selector mean 0.741935 | Sony HQ full-frame single-stream 真实语法锚点 |
| `Sony_ILCE-7M5_apsc_compressed_HQ.ARW` | raw.pixls.us Sony ILCE-7M5 | 4640x3088 | 4.762265 | 13 packets, type `{"1":4,"3":9}`, selector mean 0.741935 | Sony HQ APS-C single-stream 真实语法锚点 |
| `Nikon_Z8_high_efficiency_low.NEF` | raw.pixls.us Nikon Z 8 | 8280x5520 | 2.999930 | first Bp=4 | 当前 #826 支持路径锚点 |
| `Nikon_Z8_high_efficiency_high.NEF` | raw.pixls.us Nikon Z 8 | 8280x5520 | 4.999913 | first Bp=2 | 当前 #826 HE* unsupported 边界 |
| 7 个本地 Z7 II NEF | `C:\Users\bcm18\Downloads` | 8288x5520 | 14.007722 | no JPEG-XS SOC/CAP marker | 负样本；不能误进 Nikon HE decoder |

production-fit audit 的解释口径：

- Nikon 3.0 target 的 strict actual median 3.070839 bpp 接近 Z8 HE Bp=4 支持路径。
- Nikon 5.0 target 的 strict actual median 4.767563 bpp 接近 Z8 5 bpp/Bp=2，但这是 HE* unsupported 边界，不应写成当前 #826 支持路径。
- Sony strict canonical 的 4.0/5.0 target median 仍停在约 3.694557 bpp，离下载的 HQ 样张至少 0.773 bpp。
- Sony stream-fitted selector 敏感性实验把 selector mean 从 strict component policy 的 1.25 调到接近真实的 0.746，5.0 target median 从 3.694557 bpp 抬到 4.180130 bpp，但仍未达到公开 HQ 的 4.468/4.762 bpp；它是敏感性结果，不是 production encoder equivalence claim。

## Sony #824 已有本地样本

来源目录：`C:\libraw_arw6_test\inputs`  
探测命令：`python tools\llvc3_bitstream_probe.py <ARW> --out out\compare_reports\probe_<name>.json`

截至 2026-06-01 的外部/本地状态：

- LibRaw #824 仍为 open，head 为 `1fa7855c`，PR API 显示 4 commits、1685 additions、11 deletions、14 changed files。
- 本地 `C:\Users\姜尧耕\Downloads\LibRaw-pr-sony-arw6-craw-hq` 也是 `1fa7855c Match Adobe highlight handling in Sony ARW6 decoder`，工作区干净。
- PR review comment `discussion_r3304040579` 指出 `tiff_ifd[raw].dng_levels` 在非 DNG Sony dispatch 分支不必赋值；本地 `src/metadata/tiff.cpp` 仍有这些赋值，后续清理应作为 #824 的 open review item。

| 样本 | 场景/用途 | RAW 尺寸 | strip bytes | LLVC3 streams | magic | sequence/version | stream coded size | strip bpp | ratio vs 14-bit packed |
|---|---|---:|---:|---:|---|---:|---|---:|---:|
| `8846_full_compressed_HQ.ARW` | A7M5 full-frame CRAW HQ 官方样本；single-stream baseline | 7040x4688 | 18432512 | 1 | `A000` | 33554432 | 7040x4688 | 4.468 | 3.13 |
| `8849_apcs_compressed_hq.ARW` | A7M5 APS-C CRAW HQ 官方样本；crop/active-area baseline | 4640x3088 | 8529408 | 1 | `A000` | 33554432 | 4640x3088 | 4.762 | 2.94 |
| `ATR00049.ARW` | A7R6/R6 tiled CRAW HQ；2x2 tiles；guarded height = 3336 | 10016x6672 | 49695744 | 4 | `0000` | 16777216 | 5008x3336 | 5.949 | 2.35 |
| `DSC00157_1.ARW` | A7M5 single-stream regression；low entropy sample | 7040x4688 | 8216576 | 1 | `A000` | 16777216 | 7040x4688 | 1.992 | 7.03 |

解释：

- `strip bpp` 是 compressed strip bytes / RAW samples；它是编码效率的粗指标，类似论文里的 bits-per-pixel。
- `ratio vs 14-bit packed` 是未压缩 14-bit packed 大小与 strip 大小的比例。它更适合 RAW，因为 LibRaw/Python 输出常是 16-bit container。
- `ATR00049` 的 stream coded size 是单 tile 的 `5008x3336`；总图是 2x2 tile 拼接。
- `DSC00157_1` 的压缩比很高，主要说明场景/内容对 RAW 压缩率影响很大；不能拿单样本代表 codec 平均效率。

## Sony #824 推荐评价组合

### 结构探测

```powershell
python tools\llvc3_bitstream_probe.py C:\libraw_arw6_test\inputs\ATR00049.ARW --out out\compare_reports\probe_ATR00049.json
python tools\llvc3_bitstream_probe.py C:\libraw_arw6_test\inputs\DSC00157_1.ARW --out out\compare_reports\probe_DSC00157_1.json
```

必须检查：

- `all_packet_validations_pass=true`
- A7R6 tiled 样本 `llvc_streams` 为 4，tile 坐标覆盖 `10016x6672`
- R6 tile `logical_height=3336`，不是 16 对齐，必须走 guarded-height path
- single-stream M5 样本 `magic=A000`
- tiled R6 样本 `magic=0000`

### Python reference 与 LibRaw TIFF 比较

```powershell
python tools\compare_raw_outputs.py `
  --candidate C:\libraw_arw6_test\inputs\ATR00049.ARW.tiff `
  --reference out\libraw_perf_guard_ATR00049\ATR00049_llvc3_pure_rggb_10016x6672_u16.raw `
  --reference-shape 10016x6672 `
  --lut tools\data\sony_llvc3_static_lut4096_padded_u16.bin `
  --label ATR00049_libraw_vs_python `
  --out-json out\compare_reports\ATR00049_libraw_vs_python.json `
  --out-csv out\compare_reports\ATR00049_libraw_vs_python.csv
```

对 Adobe DNG Converter/Sony Imaging Edge 输出做比较时，应增加：

- `--candidate-crop` / `--reference-crop` 对齐共同 active area
- `--candidate-add` / `--reference-add` 对齐 black-level convention
- `--highlight-threshold 14000` 保留高光子集统计
- `--cfa-offset` 若参考输出 crop 改变了 RGGB phase

## Nikon #826 需要补齐的样本矩阵

来源目录：`C:\libraw_nikon_he_test\inputs`  
公开来源：raw.pixls.us Nikon [Z 9](https://raw.pixls.us/data/Nikon/Z%209/)、[Z 8](https://raw.pixls.us/data/Nikon/Z%208/)、[Z f](https://raw.pixls.us/data/Nikon/Z%20f/)  
探测命令：`python tools\probe_nikon_he_nef.py <NEF> --out out\compare_reports\probe_<name>.json`

截至 2026-06-01 的外部状态：

- LibRaw #826 仍为 open，head 为 `8aebd05`，PR API 显示 3 commits、4307 additions、83 deletions、42 changed files。
- #826 描述称只支持 HE，HE* 通过 first precinct `Bp` 显式拒绝；讨论里 LibRaw 维护者表示 HE/HE* 私有 decoder 可能在 2026 年秋季 public snapshot 出现。
- 对 #826 的评价还缺 #824 级别的 reference-output compare：目前样本矩阵能探测容器和 `Bp`，但还没有 NX Studio/Adobe/LibRaw private 输出上的共同 crop 逐像素统计。

`tools/probe_nikon_he_nef.py` 按 #826 当前逻辑检查 TIFF `Compression=34713`、raw strip 起始 `ff 10 ff 50` JPEG XS SOC/CAP marker，并读取 `strip + 0x9b + 3` 处的 first precinct `Bp`。#826 的启发式是：`Bp in {4,5}` 走 HE 支持路径，`Bp in {1,2,3}` 作为 HE* 显式拒绝。下面的“推断变体”是按这个 PR 逻辑得出，不等同于 Nikon UI/文件名的官方叫法。

| 本地样本 | raw.pixls 文件名 | 机型 | RAW 尺寸 | strip bytes | first Bp | #826 推断变体 | strip bpp | ratio vs 14-bit packed |
|---|---|---|---:|---:|---:|---|---:|---:|
| `Nikon_Z9_HE.NEF` | `Nikon_-_NIKON_Z_9_-_14bit_compressed_(Lossy_High_Efficiency).NEF` | NIKON Z 9 | 8280x5520 | 17139200 | 3 | HE* / unsupported by #826 | 3.000 | 4.67 |
| `Nikon_Z9_HE_star.NEF` | `Nikon_-_NIKON_Z_9_-_14bit_compressed_(Lossy_High_Efficiency_Star).NEF` | NIKON Z 9 | 8280x5520 | 28565504 | 2 | HE* / unsupported by #826 | 5.000 | 2.80 |
| `Nikon_Z8_HE_low.NEF` | `Nikon_Z8_high_efficiency_low.NEF` | NIKON Z 8 | 8280x5520 | 17139200 | 4 | HE / supported by #826 | 3.000 | 4.67 |
| `Nikon_Z8_HE_high.NEF` | `Nikon_Z8_raw_high_efficiency_hight.NEF` | NIKON Z 8 | 8280x5520 | 28565504 | 2 | HE* / unsupported by #826 | 5.000 | 2.80 |
| `Nikon_Zf_DSC_0040.NEF` | `DSC_0040.NEF` | NIKON Z f | 6064x4040 | 26971495 | 10 | not JPEG-XS HE/HE* marker | 8.808 | 1.59 |
| `Nikon_Zf_DSC_0042.NEF` | `DSC_0042.NEF` | NIKON Z f | 6064x4040 | 15311360 | 2 | HE* / unsupported by #826 | 5.000 | 2.80 |
| `Nikon_Zf_DSC_0043.NEF` | `DSC_0043.NEF` | NIKON Z f | 6064x4040 | 9186816 | 4 | HE / supported by #826 | 3.000 | 4.67 |

注意：

- raw.pixls 的 Z9 文件名 `Lossy_High_Efficiency` 探到 `Bp=3`，按 #826 当前代码会被当作 HE* 拒绝；这需要后续用 Nikon/Adobe 参考输出和真实相机菜单语义复核。
- Z8/Zf 都已有 `Bp=4` 的 #826 支持路径样本，也有 `Bp=2` 的 unsupported/HE* 路径样本。
- Z f 的 `DSC_0040.NEF` 是 `Compression=34713`，但 raw strip 起始不是 JPEG XS SOC/CAP marker；它适合作为“应回退到普通 Nikon compressed decoder、不能误进 HE decoder”的负样本。
- 还没有找到 Z 6 III / `NIKON Z6_3` 的公开 HE/HE* 样本。

#826 的 PR 描述说测试过 `8280x5520` FF HE 和 `5408x3608` DX crop HE；当前下载的公开样本覆盖 FF HE/HE* 与负样本，但 DX crop HE 还需要继续找。

建议目标矩阵：

| 样本类别 | 机型 | 模式 | 期望行为 | 参考输出 | 必测统计 |
|---|---|---|---|---|---|
| HE full frame | Z 9 | High Efficiency | 解码成功 | NX Studio / Adobe DNG / LibRaw private if available | overall/by-site/highlight diff、tone LUT、高光 |
| HE DX crop | Z 9 or Z 8 | High Efficiency DX | 解码成功，crop 正确 | NX Studio / Adobe DNG | crop alignment、tile tail、Bayer phase |
| HE full frame | Z 8 | High Efficiency | 解码成功 | NX Studio / Adobe DNG | same as Z9 |
| HE full frame | Z f | High Efficiency | 解码成功 | NX Studio / Adobe DNG | camera metadata + pixel diff |
| HE full frame | Z 6 III | High Efficiency | 解码成功 | NX Studio / Adobe DNG | camera metadata + pixel diff |
| HE* | Z 9/Z 8/Z f/Z 6 III | High Efficiency* | 当前 #826 应明确 `UNSUPPORTED_FORMAT`，不能输出坏图 | n/a | unsupported path、no partial raw output |
| corrupt/truncated | any | HE strip | 明确失败，不越界 | n/a | exception、no crash、memory guard |

Nikon HE 的关键检查点：

- JPEG XS SOC marker routing 是否会误把 HE* 送进 HE decoder。
- strip + `0x9b` fixed prefix 是否对不同机型/固件成立。
- precinct total-size、16+2 overlap、6-byte alignment pad、tail sentinel 是否有 bounds check。
- GCLI predecessor/reset、tile carry、tone LUT、Bayer step1/step2 是否能用参考输出逐像素证明。
- decode failure 语义：当前 #826 失败时会 memset raw_image 并返回，这需要和 LibRaw 错误处理惯例对齐。

静态安全审查已经单独展开到 [`libraw-826-static-safety-review.md`](libraw-826-static-safety-review.md)。样本矩阵下一步应优先为这些静态 finding 配负样本：truncated precinct、bad 6-byte pad、oversized strip、HE* first `Bp`、以及 `Compression=34713` 但无 JPEG XS marker 的 Z f `DSC_0040.NEF`。

## 与论文评价方法的对应

- JPEG XS/JPEG 2000 的 tiling/precinct/code-block 思路 -> Nikon precinct/tile 与 Sony LLVC3 tile/guard 行都必须单测。
- JPEG AI/learned codec 的 Common Test Conditions -> 建立固定样本矩阵和固定 reference 版本。
- SSIM/MS-SSIM/LPIPS 等感知指标 -> 只能用于 preview 辅助，不是 decoder conformance 主证据。
- JPEG-LS/near-lossless -> `max_abs` 和误差上界很关键。
- Real CFA compression 论文 -> 必须用真实 ARW/NEF，不应用 RGB 图合成伪 CFA 替代。
