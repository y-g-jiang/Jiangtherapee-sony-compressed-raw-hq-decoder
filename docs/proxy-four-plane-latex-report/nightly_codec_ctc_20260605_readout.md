# nightly codec CTC 20260605 判读材料

本文件对应备忘录 `overnight_smart_codec_testing_memo.md` 的 Tier A 结果。它把夜跑输出整理成第二天可直接改 `main.tex` 的证据。

## 运行状态

### A1. wide effective point cloud

输出目录：

`out/nightly_codec_ctc_20260605/wide_effective_point_cloud`

命令要点：

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

完成证据：

| 项 | 值 |
|---|---:|
| scenes | 24 |
| jobs requested | 16 |
| cpu count | 16 |
| elapsed seconds | 1392.722 |
| Sony policies | 74 |
| Sony base steps | 48 |
| Nikon offsets | 88 |
| Nikon bias count | 7 |
| Nikon GTLI row count | 20 |
| candidate rows | 380928 |
| expected rows | 380928 |
| `run.err.log` | 0 bytes |

### A2. wide frontier / CI

输出目录：

`out/nightly_codec_ctc_20260605/wide_frontier_eval`

命令要点：

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

完成证据：

| 项 | 值 |
|---|---:|
| policy unique rows | 380928 |
| jobs requested | 16 |
| bootstrap samples | 10000 |
| strict BD ok | 11/24 |
| policy-envelope BD ok | 24/24 |
| actual-bpp bins | 0.5--7.0, step 0.25 |

## 复制进报告的图

已经复制到 `docs/proxy-four-plane-latex-report/figures`：

- `fig_nightly_wide_bpp_quality_point_cloud.png`
- `fig_nightly_wide_bpp_quality_envelope.png`
- `fig_nightly_wide_bpp_quality_hexbin.png`
- `fig_nightly_wide_bd_rate_bootstrap_summary.png`
- `fig_nightly_wide_paired_actual_bpp_delta_ci.png`
- `fig_nightly_wide_actual_rate_support.png`
- `fig_nightly_wide_operational_frontier_examples.png`

`main.tex` 已改为引用 nightly wide 点云、envelope、BD-rate bootstrap 和 paired-delta CI 图。

## 点云全局范围

来自 `wide_effective_point_cloud/point_cloud_summary.csv`。

| codec | rows | policies | actual bpp range | PSNR range | median scene PSNR frontier count |
|---|---:|---:|---:|---:|---:|
| Sony #824 | 85248 | 74 | 0.242--9.111 | -1.283--90.198 dB | 234.5 |
| Nikon #826 | 295680 | 88 | 0.129--10.007 | 45.895--82.530 dB | 39.5 |

解释：

- Sony policy frontier 更密，来自 selector/component-map 和连续 `base_step` 的组合。
- Nikon 行数更多，但有效 frontier 更短，说明 GTLI/Bp/Br 台阶和 effective GTLI 复用更强。
- 全局极值不能当性能结论；正式比较看 actual-bpp bins、coverage 和 CI。

## 整数 actual-bpp bins 的 envelope

每个 bin 先在 scene×codec 内取 best PSNR，再对覆盖场景做中位数。来自 `actual_bpp_bin_envelope_summary.csv`。

| actual bpp bin | Sony coverage | Sony median best PSNR | Nikon coverage | Nikon median best PSNR | 判读 |
|---:|---:|---:|---:|---:|---|
| 1.5 | 24 | 57.430 dB | 12 | 62.978 dB | Nikon 在覆盖子集上强，但 coverage 只有一半 |
| 2.0 | 24 | 62.541 dB | 16 | 62.775 dB | 基本接近，Nikon 略高 |
| 2.5 | 24 | 64.910 dB | 15 | 64.302 dB | Sony 轻微反超 |
| 3.0 | 24 | 66.408 dB | 18 | 63.939 dB | Sony 明显高 |
| 4.0 | 20 | 69.818 dB | 12 | 63.497 dB | Sony 强 |
| 5.0 | 14 | 72.387 dB | 10 | 64.337 dB | Sony 强，但双方 coverage 都下降 |

这张表替代了“target 3/4/5 谁好”的旧读法。它显示：宽 grid 以后，Sony 不是高码率必平台；canonical 平台只是某条路径的平台。

## BD-rate / bootstrap

方向：Nikon 相对 Sony。正值表示 Nikon 达到同等 PSNR 需要更多 actual syntax bpp。

| 层级 | ok/skipped | median BD-rate | 95% CI | mean BD-rate |
|---|---:|---:|---:|---:|
| strict canonical | 11/13 | +4.758% | [-12.870%, +10.737%] | +2.171% |
| nightly wide policy envelope | 24/0 | +17.410% | [+10.239%, +44.236%] | +29.812% |

判读：

- strict canonical 仍然不能强判，CI 跨零。
- wide policy envelope 可计算 24/24，CI 不跨零，方向稳定偏 Sony。
- 这仍是 decoder-visible operational envelope，不是 production encoder optimum。

## Paired actual-bpp bins / CI

方向：Sony best PSNR 减 Nikon best PSNR。来自 `paired_actual_bpp_bin_delta_ci.csv`。

| actual bpp bin | paired scenes | median delta | 95% CI | wins Sony/Nikon | sign p |
|---:|---:|---:|---:|---:|---:|
| 1.5 | 12 | +1.621 dB | [-1.313, +2.039] | 8/4 | 0.3877 |
| 2.0 | 16 | +1.756 dB | [-0.075, +3.102] | 11/5 | 0.2101 |
| 2.5 | 15 | +1.143 dB | [-0.417, +3.474] | 11/4 | 0.1185 |
| 3.0 | 18 | +2.175 dB | [+0.466, +4.992] | 14/4 | 0.0309 |
| 4.0 | 12 | +4.059 dB | [+2.068, +9.907] | 12/0 | 0.0005 |
| 5.0 | 10 | +7.326 dB | [+3.844, +14.044] | 10/0 | 0.0020 |

判读：

- 1.5--2.5 bpp：中位偏 Sony，但 CI 覆盖零，不能写稳定胜出。
- 3.0 bpp：Sony 方向开始显著。
- 4.0--5.0 bpp：Sony wide policy envelope 明显强。

## Pareto / frontier

来自 `policy_psnr_frontier_cardinality.csv` 和 `multimetric_pareto_summary.csv`。

| codec | avg PSNR frontier count | avg PSNR frontier fraction | avg multimetric Pareto count | avg multimetric Pareto fraction |
|---|---:|---:|---:|---:|
| Sony #824 | 218.38 | 0.061 | 1428.04 | 0.402 |
| Nikon #826 | 39.21 | 0.003 | 7677.04 | 0.623 |

判读：

- Sony 的 PSNR frontier 更长，说明当前可见控制空间里能形成更多 PSNR-relevant operating points。
- Nikon 的多指标 Pareto fraction 更高，说明大量台阶点在 `rate/PSNR/MAE/MAX` 联合空间下没有被支配。
- 这支持正文继续写“best policy depends on metric”，不要把 PSNR 上包络写成唯一真实画质上包络。

## 第二天改 main.tex 的建议

已完成：

1. `main.tex` 已新增 `\NightlyWideDir`。
2. `main.tex` 已把 frontier 图换为 nightly wide：
   - `fig_nightly_wide_bd_rate_bootstrap_summary.png`
   - `fig_nightly_wide_paired_actual_bpp_delta_ci.png`
3. `main.tex` 已把点云图换为 nightly wide：
   - `fig_nightly_wide_bpp_quality_point_cloud.png`
   - `fig_nightly_wide_bpp_quality_envelope.png`
4. `main.tex` 已更新核心数字：
   - 380928 candidate points
   - 1392.722 s
   - policy envelope BD-rate +17.410%, CI [+10.239%, +44.236%]
   - paired bins 4.0/5.0 delta +4.059/+7.326 dB
   - Sony/Nikon median scene PSNR frontier count 234.5/39.5

仍需第二天人工判断：

1. 是否保留旧 `fig_frontier_*` 和 `fig_exhaustive_*` 文件作为历史附件，或从正文完全移除。
2. 是否增加 `fig_nightly_wide_actual_rate_support.png`，专门解释 high-bpp coverage 下降。
3. 是否重编译 PDF；当前环境此前没有可靠 `xelatex/latexmk`，需先跑 `latex-doctor`。
4. 是否继续 Tier B：
   - Sony full component maps
   - Nikon targeted `[-2,2]^4` offsets
   - 512x512 selected scenes

## 不能越界的写法

允许写：

> nightly wide 点云在 decoder-visible policy grid 上显示，Sony #824 的 operational PSNR envelope 从约 3 bpp 后稳定高于 Nikon #826；strict canonical 的 target-bpp 表不能代表同实际码率性能。

不允许写：

> 真实 Sony 相机编码器已经无条件优于真实 Nikon 相机编码器。

原因：

- 没有真实可控 production encoder。
- 没有完整 ARW/NEF 容器级 encoder。
- 没有 ISP/demosaic 后的感知或主观实验。
- Nikon HE* Bp=2 真实 5 bpp 路径仍是当前 unsupported 边界。
