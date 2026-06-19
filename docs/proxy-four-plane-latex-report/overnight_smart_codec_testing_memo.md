# 一晚夜跑备忘录：向 codec 论文/CTC 学习的聪明测试

日期：2026-06-05  
目标：在 roundtrip/核心语法闭合的前提下，尽可能穷举最有价值的 decoder-visible policy 空间，形成更像 codec 论文的 actual-bpp 率失真证据链。

## 一句话原则

不要再把“某个 target bpp 的一个点”当性能结论。学习 codec 论文和 CTC 的做法：固定语料和协议，用实际码流 bpp 做横轴，报告 RD 曲线、operational upper envelope、coverage、BD-rate/CI、多指标和边界。合成纹理/边缘图可以用来诊断 artifact，但不能单独代表真实照片分布。

## 从文献/CTC 学什么

1. **Common Test Conditions**
   JPEG AI CTC 明确要求固定数据集、anchors、目标码率、客观和主观质量评价程序。我们对应为：固定 24 个同源 RGGB 场景、固定 seed、固定 actual-bpp bins、固定 #824/#826 decoder-visible syntax/math，并把真实 Sony/Nikon 样张作为 syntax anchor。

2. **实际 bpp 而非 target bpp**
   JPEG AI evaluation procedure 把 bpp 定义为压缩表示总 bit 数除以像素数。我们对应为：所有性能图横轴只用 `actual syntax bpp`；target bpp 只能作为 rate-control 诊断标签。

3. **RDO / operational RD set**
   编码论文通常不声称穷举所有私有 RDO，而是报告可复现 operating points、envelope、ablation/sensitivity。我们对应为：枚举 selector、base-step、GTLI/Bp/Br、component offsets 的 decoder-visible 子空间；不声称 Sony/Nikon production encoder optimum。

4. **Subband gains/priorities**
   JPEG XS gains/priorities 文献说明同一 codec 可以针对 PSNR、MS-SSIM、任务精度调不同 subband weights。我们对应为：把 green/RB/phase/detail/smooth 等 allocation 当作 policy 变量，报告同 bpp 下质量分叉。

5. **Distortion-perception tradeoff**
   Blau/Michaeli R-D-P 提醒 PSNR 最优不是视觉最优。今晚主跑 RAW sample-domain 点云；第二天只允许说 sample-domain/effective reconstruction 结论，不写主观画质终局。

6. **纹理/边缘/渐变 stress tests**
   Camera/image-quality 文献常用 slanted edge、dead leaves/random texture、zone plate、color bars 等诊断图。我们已有 `fine_texture`、`random_foliage`、`zone_plate`、`nyquist_checker`、`thin_black_lines`、`smooth_gradient`、`red_blue_fine_text`、`green_phase_alias`，定位应是 artifact stress set，而非真实照片分布。

## main.tex 当前成果与需要重跑的原因

`docs/proxy-four-plane-latex-report/main.tex` 已经汇总了很多阶段成果：

- strict canonical 评估：`out/strict_824_826_math_eval_full_20260603`
- stage-separated insight：`out/strict_824_826_math_insight_20260603`
- metric validation：`out/strict_824_826_metric_validation`
- minimal bitstream closure：`out/strict_824_826_minimal_bitstream_closure`
- production-fit samples：`out/production_fit_samples`
- Sony real-HQ selector sensitivity：`out/sony_stream_fitted_selector_eval_20260604`
- policy multiplicity：`out/bpp_policy_multiplicity_20260604`
- actual-bpp RD insights：`out/bpp_policy_multiplicity_20260604/actual_bpp_rd_insights`
- frontier-level bootstrap/Pareto：`out/bpp_policy_multiplicity_20260604/frontier_math_eval_20260605`
- current-policy 点云：`out/exhaustive_bpp_quality_cloud_current_20260605`

但是很多需要重跑或至少重新后处理：

1. `main.pdf` 时间早于新增点云/frontier 内容，PDF 不是最新正文。
2. 旧 strict 与 insight 目录使用 20260603 seed/输出，后续脚本已经新增 actual-bpp/frontier/point-cloud 逻辑，应统一到一套 nightly manifest。
3. 旧图表脚本曾生成 target 横轴图，虽然正文已替换，但重跑时必须再次检查所有性能图横轴。
4. 点云脚本新增了 Nikon effective-GTLI 缓存；旧 `wide_20260605` 是中断目录，不可作为结果。
5. 后续最终报告应引用 nightly 目录，而不是混合多个日期目录。

## 夜跑目录约定

统一根目录：

```text
out/nightly_codec_ctc_20260605
```

建议每个实验都有：

- `run.log`
- `run.err.log`
- `manifest.json`
- CSV outputs
- figures

所有命令都显式 `--jobs 16` 或合理留一核 `--jobs 15`。脚本内部已经把 BLAS/OpenMP 线程限制为 1，避免进程池内再超额抢线程。

## 必跑 Tier A：最高价值，今晚先跑

### A0. 代码与现有 artifact 预检查

```powershell
python -m py_compile `
  tools\strict_824_826_math_eval.py `
  tools\strict_824_826_insight_eval.py `
  tools\policy_sweep_bpp_multiplicity.py `
  tools\actual_bpp_rd_insights.py `
  tools\frontier_math_eval.py `
  tools\exhaustive_bpp_quality_cloud.py
```

检查目标：

- Python 语法通过。
- 没有残留长跑进程。
- `out/production_fit_samples/real_bitstream_controls.csv` 存在。

### A1. 宽点云：最值得穷举的 policy 子空间

这是今晚最重要的实验。它比 current 点云更接近“尽可能遍历”，但仍控制在一晚能跑完的范围。

```powershell
python tools\exhaustive_bpp_quality_cloud.py `
  --out-dir out\nightly_codec_ctc_20260605\wide_effective_point_cloud `
  --sony-policy-mode current-plus-maps `
  --sony-tie-rb `
  --sony-base-step-count 48 `
  --nikon-offset-mode current-plus-grid `
  --nikon-offset-values -1,0,1 `
  --nikon-bias-count 7 `
  --jobs 16 `
  --max-scatter-points 240000
```

预计规模：

| 项 | 数量 |
|---|---:|
| scenes | 24 |
| Sony policies | 74 |
| Sony base steps | 48 |
| Sony rows | 85248 |
| Nikon offsets | 88 |
| Nikon bias | 7 |
| Nikon GTLI rows | 20 |
| Nikon rows | 295680 |
| total rows | 380928 |

为什么这是最有价值的穷举：

- Sony：加入 tied-RB 的 4^3 component selector maps，再保留当前 row-cycle/real-like policies。
- Nikon：加入 `[-1,0,1]^4` component-offset grid，覆盖 green/detail/RB/smooth 的主要 allocation 方向。
- 横轴天然是 actual syntax bpp。
- 输出直接给 bpp--质量点云、hexbin、bin envelope。

边界：

- Sony `base_step` 仍是连续量，48 点是密扫，不是数学穷尽。
- Nikon 相同 effective GTLI tuple 会缓存复用，所以这是 effective reconstruction cloud，不是冗余 header bit 完全枚举。

### A2. 宽点云的 frontier/CI 后处理

A1 完成后立刻跑：

```powershell
python tools\frontier_math_eval.py `
  --policy-csv out\nightly_codec_ctc_20260605\wide_effective_point_cloud\point_cloud_candidates.csv `
  --strict-metrics out\strict_824_826_math_eval_full_20260603\metrics.csv `
  --out-dir out\nightly_codec_ctc_20260605\wide_frontier_eval `
  --jobs 16 `
  --bootstrap-samples 10000 `
  --bin-start 0.5 `
  --bin-stop 7.0 `
  --bin-step 0.25 `
  --bin-tolerance 0.125
```

判读重点：

- `bd_rate_bootstrap_summary.csv`
- `paired_actual_bpp_bin_delta_ci.csv`
- `actual_rate_support.csv`
- `policy_psnr_frontier_cardinality.csv`
- `multimetric_pareto_summary.csv`

### A3. 复制图到报告目录

```powershell
Copy-Item out\nightly_codec_ctc_20260605\wide_effective_point_cloud\fig_bpp_quality_point_cloud.png `
  docs\proxy-four-plane-latex-report\figures\fig_nightly_wide_bpp_quality_point_cloud.png

Copy-Item out\nightly_codec_ctc_20260605\wide_effective_point_cloud\fig_bpp_quality_envelope.png `
  docs\proxy-four-plane-latex-report\figures\fig_nightly_wide_bpp_quality_envelope.png

Copy-Item out\nightly_codec_ctc_20260605\wide_frontier_eval\fig_bd_rate_bootstrap_summary.png `
  docs\proxy-four-plane-latex-report\figures\fig_nightly_wide_bd_rate_bootstrap_summary.png

Copy-Item out\nightly_codec_ctc_20260605\wide_frontier_eval\fig_paired_actual_bpp_delta_ci.png `
  docs\proxy-four-plane-latex-report\figures\fig_nightly_wide_paired_actual_bpp_delta_ci.png
```

## Tier B：如果 A1/A2 在半夜前完成

### B1. Sony-only 完整 component selector maps

Sony 的 selector map 空间更连续，也更可能解释 high-bpp envelope。这个实验放开 R/B 绑定，扫完整 4^4 component map。

```powershell
python tools\exhaustive_bpp_quality_cloud.py `
  --out-dir out\nightly_codec_ctc_20260605\sony_full_component_maps `
  --codec sony `
  --sony-policy-mode maps `
  --sony-base-step-count 96 `
  --jobs 16 `
  --max-scatter-points 240000
```

预计 rows：24 × 256 × 96 = 589824。

目的：

- 检查 `Rres2` 和 `Bres2` 不绑定时，红蓝细节、color edges、red_blue_fine_text 是否出现更优 frontier。
- 判断当前 tied-RB 近似有没有压低 Sony 上包络。

### B2. Nikon targeted wider offsets on stress scenes

不要全 24 场景直接上 `[-2,2]^4`，先选最有解释价值的场景：

```text
fine_texture,shadow_noise,highlight_rolloff,red_blue_fine_text,green_phase_alias,color_edges,zone_plate,thin_black_lines,skin_like_smooth,low_contrast_detail
```

```powershell
python tools\exhaustive_bpp_quality_cloud.py `
  --out-dir out\nightly_codec_ctc_20260605\nikon_targeted_offsets_m2p2 `
  --codec nikon `
  --scenes fine_texture,shadow_noise,highlight_rolloff,red_blue_fine_text,green_phase_alias,color_edges,zone_plate,thin_black_lines,skin_like_smooth,low_contrast_detail `
  --nikon-offset-mode grid `
  --nikon-offset-values -2,-1,0,1,2 `
  --nikon-bias-count 8 `
  --jobs 16 `
  --max-scatter-points 240000
```

预计 rows：10 × 625 × 8 × 20 = 1000000。  
由于 effective-GTLI 缓存，重建点数量会少很多，但 CSV 行数仍大。若磁盘或时间紧，先把 `--nikon-bias-count` 降到 6。

目的：

- 检查 Nikon 在 4--5 bpp bin 是否只是 current offsets 没搜到，还是 GTLI/Bp/Br 结构确实弱。
- 检查平滑/高光/暗部优势是否随更宽 offsets 保持。

## Tier C：如果还有时间

### C1. 512x512 尺寸稳定性抽查

当前主数据是 256×256。为了避免小图语法 overhead 或纹理周期影响结论，选 6 个场景做 512×512 current-plus-maps smoke：

```powershell
python tools\exhaustive_bpp_quality_cloud.py `
  --out-dir out\nightly_codec_ctc_20260605\size512_selected_cloud `
  --width 512 `
  --height 512 `
  --scenes fine_texture,shadow_noise,highlight_rolloff,red_blue_fine_text,green_phase_alias,zone_plate `
  --sony-policy-mode current-plus-maps `
  --sony-tie-rb `
  --sony-base-step-count 32 `
  --nikon-offset-mode current-plus-grid `
  --nikon-offset-values -1,0,1 `
  --nikon-bias-count 7 `
  --jobs 16 `
  --max-scatter-points 200000
```

目的：

- 检查点云形状是否随图像尺寸变化。
- 检查 syntax overhead 对低 bpp bin 的影响。

### C2. 真实 Sony HQ selector 约束再跑

如果 A/B 显示 Sony high-bpp envelope 由 selector map 主导，应补一个 real-HQ selector-constrained 点云：

```powershell
python tools\exhaustive_bpp_quality_cloud.py `
  --out-dir out\nightly_codec_ctc_20260605\sony_real_hq_selector_constrained `
  --codec sony `
  --sony-policy-mode current `
  --sony-base-step-count 96 `
  --real-controls out\production_fit_samples\real_bitstream_controls.csv `
  --jobs 16 `
  --max-scatter-points 200000
```

目的：

- 把 “Sony theoretical selector maps” 和 “real HQ selector histogram/cycle” 分开。
- 避免把不真实的 selector allocation 写成 production-like。

## 不建议今晚做的事

1. 不要全量跑 `[-2,-1,0,1,2]^4` × 24 scenes × 10+ bias，CSV 会非常大，且很多 redundant effective GTLI。
2. 不要把 Bp/Br 冗余 header row 全当不同质量点。它们可能只改微小 syntax cost，不改重建；先用 effective reconstruction cloud。
3. 不要把 LPIPS/VMAF/MOS 强塞进 RAW sample-domain。没有统一 demosaic/ISP/display pipeline 前，这些数值很容易误导。
4. 不要重新生成 target-bpp 横轴图。任何性能图都必须是 actual bpp。
5. 不要把真实 Sony HQ 或 Nikon Z8 单样张写成性能曲线；它们只是 syntax/rate anchor。

## 夜跑监控方式

建议后台启动时用日志：

```powershell
$out = 'out\nightly_codec_ctc_20260605\wide_effective_point_cloud'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$log = Join-Path $out 'run.log'
$err = Join-Path $out 'run.err.log'
$args = @(
  'tools\exhaustive_bpp_quality_cloud.py',
  '--out-dir', $out,
  '--sony-policy-mode', 'current-plus-maps',
  '--sony-tie-rb',
  '--sony-base-step-count', '48',
  '--nikon-offset-mode', 'current-plus-grid',
  '--nikon-offset-values', '-1,0,1',
  '--nikon-bias-count', '7',
  '--jobs', '16',
  '--max-scatter-points', '240000'
)
Start-Process -FilePath 'python' -ArgumentList $args -WorkingDirectory (Get-Location) `
  -RedirectStandardOutput $log -RedirectStandardError $err -WindowStyle Hidden
```

轮询：

```powershell
Get-Content out\nightly_codec_ctc_20260605\wide_effective_point_cloud\run.log -Tail 80
Get-Content out\nightly_codec_ctc_20260605\wide_effective_point_cloud\run.err.log -Tail 80
Get-Process python -ErrorAction SilentlyContinue | Sort-Object CPU -Descending | Select-Object -First 12 Id,CPU,StartTime
```

完成条件：

- `manifest.json` 存在。
- `candidate_rows == expected_candidate_rows`。
- `run.err.log` 为空或只有非致命 warning。
- `point_cloud_candidates.csv` 不包含 `target_bpp` 列。
- 图表横轴为 actual syntax bpp。

## 第二天判读顺序

1. 读 `manifest.json`，确认 rows、jobs、elapsed、boundary。
2. 读 `point_cloud_summary.csv`，看全局 bpp/PSNR 范围和 frontier count。
3. 读 `actual_bpp_bin_envelope_summary.csv`，只比较 coverage 足够的 bins。
4. 读 `wide_frontier_eval/paired_actual_bpp_bin_delta_ci.csv`，看 CI 是否跨零。
5. 读 `wide_frontier_eval/bd_rate_bootstrap_summary.csv`，看 ok/skipped 和 CI。
6. 和 `main.tex` 旧结论逐条对照：哪些保留，哪些被宽点云推翻，哪些只需改写为边界。
7. 只把 confirmed results 复制进 `docs/proxy-four-plane-latex-report/figures`。
8. 更新 `main.tex`，然后若本机有 XeLaTeX，重新编译 PDF。

## 第二天必须写进正文的边界

推荐表述：

> 本文的 overnight 点云学习 codec CTC 的做法：固定 source set、固定 decoder-visible syntax/math、使用 actual syntax bpp、报告 operational envelope 和 coverage。它证明 bpp 并非唯一编码解，也暴露 Sony selector/base-step 与 Nikon GTLI/Bp/Br allocation 的不同控制形态。但该点云仍不是 Sony/Nikon production encoder 的私有 RDO 穷举；真实编码器胜负还需要可控真实码流、多图真实照片分布、ISP 后感知评价和主观测试。

## 参考入口

- JPEG AI CTC: https://jpeg.org/items/20201028_jpeg_ai_common_test_conditions.html
- JPEG AI evaluation procedure: https://jpegai.github.io/4-eval_proc/
- JPEG XS gains/priorities: https://openaccess.thecvf.com/content_CVPRW_2020/html/w7/Brummer_Adapting_JPEG_XS_Gains_and_Priorities_to_Tasks_and_Contents_CVPRW_2020_paper.html
- Rate-distortion-perception tradeoff: https://proceedings.mlr.press/v97/blau19a.html
- Texture/detail diagnostic background: https://www.imatest.com/imaging/texture/
