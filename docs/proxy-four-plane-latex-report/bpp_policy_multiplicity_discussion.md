# bpp 非唯一性与同码率质量范围补充材料

## 核心结论

给定实际 bpp 不是唯一编码解。bpp 只是 rate outcome；真正决定重建质量的是在这个 rate 附近怎样分配 bits：Sony #824 路径里有 selector map、row-cycle selector、base step、payload/sign/width/zero-run 成本之间的折中；Nikon #826 路径里有 Bp/Br、GTLI/GCLI row、precinct/component bias、detail/green/RB allocation 的折中。即便把搜索限制在 decoder-visible syntax/math 里的有限 policy grid，同实际 bpp 附近也会出现 multi-dB 的 PSNR 质量范围。

因此正文里不能再把“某个 bpp 上的一个 PSNR 点”写成 codec 的唯一质量。更严谨的写法应当是：

1. canonical 曲线只是一个可审计、可复现的轨迹，不是唯一 RD frontier。
2. 同 bpp 比较应以 `actual syntax bpp` 或实际文件空间为横轴，并报告每个 codec 的质量区间 `[min, max]` 和区间上界 `best`。
3. 若要比较 #824 与 #826，优先比较二者在同 actual-bpp 窗口内的 upper envelope；窗口覆盖不足的场景必须标注，而不能被混入强结论。

## 模拟设置

完整 policy sweep 使用：

```powershell
python tools\policy_sweep_bpp_multiplicity.py `
  --out-dir out\bpp_policy_multiplicity_20260604 `
  --jobs 16 `
  --width 256 `
  --height 256 `
  --nikon-bias-count 7 `
  --rate-tolerance 0.08
```

后处理质量范围图使用：

```powershell
python tools\summarize_bpp_policy_ranges.py `
  --candidate-csv out\bpp_policy_multiplicity_20260604\policy_candidates.csv `
  --out-dir out\bpp_policy_multiplicity_20260604\quality_range_summary `
  --rate-tolerance 0.20
```

`manifest.json` 记录完整 sweep 为 24 个确定性 RGGB 场景、6 个请求标签、10 个 Sony policy、7 个 Nikon policy，共 2448 个候选点。运行时请求 16 个 worker，本机逻辑 CPU 数为 16，完整重计算耗时 1660.64 秒。脚本内部把 BLAS/OpenMP 线程限制为 1，并把进程池任务拆成 scene × policy 粒度，避免每个 worker 内部再超额抢线程。

## canonical 附近的同 bpp 离散度

下面这张表使用完整 sweep 中的 `policy_dispersion_summary.csv`。窗口是 canonical 实际 bpp 附近的 ±0.08 actual bpp，回答的是：“保持与 canonical 点近似同实际码率时，换 policy 能让质量移动多少？”表中的 target 只是定位 canonical operating point 的请求标签，不是性能横轴。

| 请求标签 | #824 median spread | #824 median best gain | #826 median spread | #826 median best gain |
|---:|---:|---:|---:|---:|
| 1.5 | 5.243 dB | +3.517 dB | 4.689 dB | +0.565 dB |
| 2.0 | 4.579 dB | +2.551 dB | 4.740 dB | +0.092 dB |
| 2.5 | 4.023 dB | +2.071 dB | 3.112 dB | +0.407 dB |
| 3.0 | 3.534 dB | +1.404 dB | 1.355 dB | +0.279 dB |
| 4.0 | 0.301 dB | +0.000 dB | 1.133 dB | +0.000 dB |
| 5.0 | 0.291 dB | +0.000 dB | 0.000 dB | +0.000 dB |

这已经足够说明 bpp 不是唯一解：在低中码率段，同实际 bpp 附近的 policy 变化能带来约 3 到 5 dB 的中位区间宽度。高码率端的较小区间不代表唯一性消失，而是当前有限 policy grid 和可达 rate 平台收窄了同码率候选。

## actual-bpp 轴上的逐场景质量范围

为了回答“不同场景下同实际 bpp，#824 和 #826 各自有一个质量范围和最优值”，以 `tools/actual_bpp_rd_insights.py` 对 actual-bpp bins 做 paired comparison。每个 bin 只统计 #824 与 #826 同时覆盖的同一批 scenes，方向为 Sony #824 best PSNR 减 Nikon #826 best PSNR。这样避免 target 横轴，也避免不同 bin 覆盖场景不同造成的假结论。

actual-bpp 主图：

| 图 | 用途 |
|---|---|
| `out/bpp_policy_multiplicity_20260604/actual_bpp_rd_insights/fig_actual_bpp_canonical_median_rd.png` | strict canonical 中位 RD 轨迹，横轴为 actual syntax bpp |
| `out/bpp_policy_multiplicity_20260604/actual_bpp_rd_insights/fig_actual_bpp_policy_range_envelope.png` | actual-bpp bins 内的 policy range 与 upper envelope |
| `out/bpp_policy_multiplicity_20260604/actual_bpp_rd_insights/fig_actual_bpp_policy_paired_delta.png` | 同一 actual-bpp bin、同一批 paired scenes 的 #824/#826 best delta |

actual-bpp paired bins 的关键结果如下。`coverage` 表示 24 个场景中有多少场景双方 codec 在该 actual-bpp bin 内都有候选点。

| actual bpp bin ±0.20 | paired scenes | median best delta | best wins #824/#826 |
|---:|---:|---:|---:|
| 1.5 | 11/24 | +0.861 dB | 6/5 |
| 2.0 | 13/24 | +0.553 dB | 8/5 |
| 2.5 | 12/24 | +0.489 dB | 8/4 |
| 3.0 | 14/24 | +1.097 dB | 9/5 |
| 4.0 | 13/24 | +2.080 dB | 12/1 |
| 5.0 | 12/24 | +5.122 dB | 12/0 |

这张表仍要谨慎解释：高码率 bins 的 paired coverage 较低，说明可达点稀疏；它不是 production encoder 最优证明。但它比 target-window 表更接近用户问题，因为横轴是 actual syntax bpp。

## 该结论怎样进入正文

建议把原来的同目标请求段之后加一段“bpp 非唯一性与 policy frontier”。核心表述可以写成：

> 同一个 requested bpp 或近似 actual bpp 并不对应唯一重建质量。decoder-visible syntax 里仍有多种 encoder policy 可以在相近 rate 下重新分配 bits。完整 sweep 在 24 个同源 RGGB 场景上显示，#824 与 #826 在同 actual-bpp 窗口内都存在 multi-dB 质量区间。因此 canonical 曲线只代表一个可审计轨迹；公平比较应报告 codec-specific quality interval 和 upper envelope，而不能把单一 canonical 点解释为 production encoder 的 RD 最优点。

然后接图：

1. `fig_actual_bpp_canonical_median_rd.png` 用来替代所有 target-bpp 横轴性能图。
2. `fig_actual_bpp_policy_paired_delta.png` 用来说明同 actual-bpp bins 内 paired scenes 的上包络差异。
3. 表格中 paired delta 只对双 codec 同时落入 actual-bpp bin 的场景成立；coverage 不足处只能作为可达性/离散台阶证据，不能作为强同码率胜负。

## 边界

这仍然不是 production encoder optimum。policy grid 只枚举了当前可由 decoder-visible syntax 合理构造的 selector/GTLI 分配策略，没有声称覆盖 Sony 或 Nikon 的私有 RD search、per-tile lambda、内容分类器、色彩感知权重或容器级码率控制。因此这些质量区间应被解释为“当前 strict 可审计模型已经证明 bpp 非唯一”，而不是“真实相机的完整 Pareto frontier 已经穷尽”。
