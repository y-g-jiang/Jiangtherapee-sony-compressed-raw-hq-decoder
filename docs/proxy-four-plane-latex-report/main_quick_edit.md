# strict #824/#826 大报告 quick edit

日期：2026-06-03

最终论文源：`docs/proxy-four-plane-latex-report/main.tex`

最终编译 PDF：`docs/proxy-four-plane-latex-report/main.pdf`

最终评估目录：`out/strict_824_826_math_eval_full_20260603`

核心机器摘要：`out/strict_824_826_math_eval_full_20260603/paper_numbers.json`

指标验证闭环：`out/strict_824_826_metric_validation/metric_reference_validation.json`

最小核心码流闭环：`out/strict_824_826_minimal_bitstream_closure/bitstream_closure.json`

## 本轮修正目标

大报告保留旧 11 页报告的完整评价体系，但所有主数据改用最新 strict #824/#826 decoder-visible canonical encoder 结果。不再把旧 L2/L2.5 proxy 的历史 BD-rate 或 coded-bpp 数字写作主结论。

## 允许与不允许的 claim

允许：`decoder-visible canonical simulation`。

不允许：`production encoder equivalence claim`。

原因：`out/strict_824_826_encoder_reversibility/audit.json` 中 `allows_production_encoder_equivalence_claim=false`。审计计数为 `exact reverse=8`、`canonical choice=2`、`not decoder determined=2`。

## 最新 strict 批次

- Nikon LUT 使用 `sample14` 列参与 RAW sample 域误差计算。
- Nikon dequant 使用 LibRaw #826 `kMidpointScaleTable` 常数。
- Sony 侧纳入 #824 packet selector、adaptive width、zero-run、magnitude/sign 与 final-green/RB residual。
- strict 评估脚本已按 scene 多核心化，完整批次使用 `--jobs 24`。
- 指标补齐 `MS-SSIM` 与 `GMSD`，`metrics.csv` 为 7488 行。

## 最终实验命令

```powershell
python tools\audit_824_826_encoder_reversibility.py --out out\strict_824_826_encoder_reversibility\audit.json
python tools\validate_strict_metric_references.py
python tools\validate_824_826_minimal_bitstream_closure.py
python tools\strict_824_826_math_eval.py --out-dir out\strict_824_826_math_eval_full_20260603 --width 256 --height 256 --levels 3 --targets 1.5,2.0,2.5,3.0,4.0,5.0 --seed 20260603 --jobs 24
python tools\compute_bd_rate.py --metrics out\strict_824_826_math_eval_full_20260603\metrics.csv --codec-a nikon_826_decoder_visible_precinct_canonical --codec-b sony_824_decoder_visible_packet_canonical --metric PSNR_raw --group-field split --out out\strict_824_826_math_eval_full_20260603\bd_rate_psnr.csv
python tools\compute_bd_rate.py --metrics out\strict_824_826_math_eval_full_20260603\metrics.csv --codec-a nikon_826_decoder_visible_precinct_canonical --codec-b sony_824_decoder_visible_packet_canonical --metric MAE --lower-is-better --group-field split --out out\strict_824_826_math_eval_full_20260603\bd_rate_mae.csv
python tools\compute_bd_rate.py --metrics out\strict_824_826_math_eval_full_20260603\metrics.csv --codec-a nikon_826_decoder_visible_precinct_canonical --codec-b sony_824_decoder_visible_packet_canonical --metric grad_psnr --group-field split --out out\strict_824_826_math_eval_full_20260603\bd_rate_grad_psnr.csv
python tools\compute_bd_rate.py --metrics out\strict_824_826_math_eval_full_20260603\metrics.csv --codec-a nikon_826_decoder_visible_precinct_canonical --codec-b sony_824_decoder_visible_packet_canonical --metric ssim_mean --group-field split --out out\strict_824_826_math_eval_full_20260603\bd_rate_ssim.csv
python tools\compute_bd_rate.py --metrics out\strict_824_826_math_eval_full_20260603\metrics.csv --codec-a nikon_826_decoder_visible_precinct_canonical --codec-b sony_824_decoder_visible_packet_canonical --metric ms_ssim_mean --group-field split --out out\strict_824_826_math_eval_full_20260603\bd_rate_ms_ssim.csv
python tools\compute_bd_rate.py --metrics out\strict_824_826_math_eval_full_20260603\metrics.csv --codec-a nikon_826_decoder_visible_precinct_canonical --codec-b sony_824_decoder_visible_packet_canonical --metric gmsd_mean --lower-is-better --group-field split --out out\strict_824_826_math_eval_full_20260603\bd_rate_gmsd.csv
python tools\summarize_strict_824_826_math_eval.py --math-dir out\strict_824_826_math_eval_full_20260603 --audit out\strict_824_826_encoder_reversibility\audit.json
python tools\make_strict_824_826_latex_report_figures.py --math-dir out\strict_824_826_math_eval_full_20260603 --fig-dir docs\proxy-four-plane-latex-report\figures --roi-jobs 5
python tools\strict_824_826_insight_eval.py --out-dir out\strict_824_826_math_insight_20260603 --width 256 --height 256 --levels 3 --targets 1.5,2.0,2.5,3.0,4.0,5.0 --seed 20260603 --jobs 24
python tools\compute_bd_rate.py --metrics out\strict_824_826_math_insight_20260603\insight_metrics.csv --codec-a nikon_826_decoder_visible_precinct_canonical --codec-b sony_824_decoder_visible_packet_canonical --metric vifp_mean --group-field split --out out\strict_824_826_math_insight_20260603\bd_rate_vifp.csv
python tools\compute_bd_rate.py --metrics out\strict_824_826_math_insight_20260603\stage_metrics.csv --codec-a nikon_826_decoder_visible_precinct_canonical --codec-b sony_824_decoder_visible_packet_canonical --metric coeff_SNR_db --group-field stage --group-field split --out out\strict_824_826_math_insight_20260603\bd_rate_coeff_snr.csv
python tools\make_strict_824_826_latex_report_figures.py --math-dir out\strict_824_826_math_eval_full_20260603 --insight-dir out\strict_824_826_math_insight_20260603 --fig-dir docs\proxy-four-plane-latex-report\figures --roi-jobs 5
```

## 关键数字

- 规模：24 个合成 RAW 场景，256x256 RAW，6 个目标请求 bpp，两条 canonical codec，288 次编码，7488 条质量指标，288 条 syntax 记录，48 条 roundtrip 审计。
- whole PSNR BD-rate：Nikon 相对 Sony 中位 `+4.758%`，可算 `11/24`，分位区间 `-29.635%` 到 `+46.572%`。
- whole MAE BD-rate：中位 `+13.614%`。
- detail grad-PSNR、SSIM、MS-SSIM、GMSD BD-rate：分别为 `+0.828%`、`+1.416%`、`+5.598%`、`+0.237%`。
- 请求 2.5 bpp：Sony 实际中位 2.4991 bpp、PSNR 60.848 dB；Nikon 实际中位 2.6151 bpp、PSNR 61.868 dB。
- 请求 3.0 bpp：Sony 实际中位 2.9982 bpp、PSNR 64.015 dB；Nikon 实际中位 3.0708 bpp、PSNR 63.206 dB。
- insight 目录：`out/strict_824_826_math_insight_20260603`，stage 3504 行、insight 3168 行、encodes 288 行、RD slope segments 151 行。
- 分层评价：transform roundtrip 中位 MAE/MAX 为 Sony 2.815/10 DN、Nikon 0.000153/1 DN；2.5 bpp coeff SNR 为 Sony 44.014 dB、Nikon 35.715 dB。
- 洞见指标：VIF-style BD-rate 为 `+41.731%`，可算 `7/24`；coeff SNR BD-rate 为 `+66.045%`，可算 `11/24`。

## 结论写法

正确写法：两套 decoder-visible canonical encoder 都能形成 strict 数学评价链，优势随码率点、指标和场景变化；当前证据支持 canonical 层面的可复现比较。

错误写法：已经复刻真实 Sony/Nikon production encoder，或已经证明某一方在真实相机编码效率上稳定无条件胜出。
