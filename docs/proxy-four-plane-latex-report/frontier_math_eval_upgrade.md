# frontier-level 数学评价升级说明

## 先回答问题

之前的数学评价还没有达到前沿编码文献的完整能力。它已经有同源输入、实际 syntax bpp、PSNR/SSIM/MS-SSIM/GMSD/VIF-style、BD-rate 和局部 RD 斜率，但仍偏向“画出一组 canonical 曲线”。前沿 codec 评价更强调：

1. 真实 rate 轴：横轴必须是实际码流或文件空间，不是 target bpp。
2. operational RD set：一个 codec 在同一 rate 附近有许多可行 policy，canonical 曲线不能代表上包络或 Pareto frontier。
3. 统计可信度：BD-rate 和 bin-wise delta 需要 per-scene 分布、skip reason、bootstrap CI 或显著性检验。
4. 多目标 Pareto：PSNR、MAE、MAX、SSIM/MS-SSIM/GMSD、感知指标、任务指标可能选择不同 policy。
5. 复杂度与主观边界：延迟、内存、编码复杂度、demosaic/ISP 后的观察者质量和真实 production encoder 仍是本文当前缺口。

我这次把第 1 到第 4 点补强到当前 artifact 能支持的程度；第 5 点明确写为边界，不伪装成已经完成。

## 新增评估

脚本：

```powershell
python tools\frontier_math_eval.py `
  --jobs 16 `
  --bootstrap-samples 5000 `
  --out-dir out\bpp_policy_multiplicity_20260604\frontier_math_eval_20260605
```

manifest 记录：

| 项 | 值 |
|---|---:|
| jobs requested | 16 |
| cpu count | 16 |
| bootstrap samples | 5000 |
| policy unique rows | 1820 |
| policy scene count | 24 |
| strict scene count | 24 |
| strict BD-rate ok | 11/24 |
| policy-envelope BD-rate ok | 22/24 |

输出目录：`out/bpp_policy_multiplicity_20260604/frontier_math_eval_20260605`

正文可直接引用的图已经复制到：

- `docs/proxy-four-plane-latex-report/figures/fig_frontier_bd_rate_bootstrap_summary.png`
- `docs/proxy-four-plane-latex-report/figures/fig_frontier_paired_actual_bpp_delta_ci.png`
- `docs/proxy-four-plane-latex-report/figures/fig_frontier_actual_rate_support.png`
- `docs/proxy-four-plane-latex-report/figures/fig_frontier_operational_examples.png`

## 核心结果

### BD-rate with bootstrap CI

方向：Nikon #826 相对 Sony #824。正值表示 Nikon 达到同等 PSNR 需要更多 actual syntax bpp。

| 评估对象 | ok/skipped | median BD-rate | median 95% CI | mean BD-rate |
|---|---:|---:|---:|---:|
| strict canonical | 11/13 | +4.758% | [-12.870%, +10.737%] | +2.171% |
| policy PSNR envelope | 22/2 | +11.118% | [+1.679%, +18.111%] | +23.005% |

解读：strict canonical 的置信区间跨零，不能单独写成稳定胜负；但在枚举 policy 的 PSNR operational upper envelope 上，可计算场景扩大到 22/24，中位方向更稳定地偏向 Sony。这个结论仍然只限于 decoder-visible policy grid，不等于真实相机 firmware RDO。

### actual-bpp paired bins with CI

方向：Sony best PSNR 减 Nikon best PSNR。每个 bin 只统计双方 codec 同时覆盖的同一批场景。

| actual bpp bin ±0.20 | paired scenes | median delta | 95% CI | wins Sony/Nikon | sign p |
|---:|---:|---:|---:|---:|---:|
| 1.5 | 11 | +0.861 dB | [-2.441, +1.544] | 6/5 | 1.0000 |
| 2.0 | 13 | +0.553 dB | [-1.687, +1.239] | 8/5 | 0.5811 |
| 2.5 | 12 | +0.489 dB | [-1.096, +1.756] | 8/4 | 0.3877 |
| 3.0 | 14 | +1.097 dB | [-0.811, +3.011] | 9/5 | 0.4240 |
| 4.0 | 13 | +2.080 dB | [+1.273, +8.323] | 12/1 | 0.0034 |
| 5.0 | 12 | +5.122 dB | [+2.081, +9.254] | 12/0 | 0.0005 |

解读：低中 bpp 的 CI 覆盖零，只能说方向不稳定或场景依赖。4.0 和 5.0 actual-bpp bins 上，当前 policy envelope 给出较强 Sony 方向。但这些高 bpp bins 的 coverage 仍只有约一半场景，必须和可达率一起报告。

### actual-rate support

rate support 回答的是“某 codec 在该实际 bpp 窗口内有没有可比较点”。这比 target 表更接近用户真正关心的“用了多少空间”。

| bin | Sony coverage | Nikon coverage |
|---:|---:|---:|
| 1.5 | 24/24 | 11/24 |
| 2.0 | 24/24 | 13/24 |
| 2.5 | 24/24 | 12/24 |
| 3.0 | 24/24 | 14/24 |
| 4.0 | 20/24 | 13/24 |
| 5.0 | 14/24 | 12/24 |

解读：Sony 在整数 actual-bpp bins 的可达覆盖更密，Nikon 在若干半步/台阶处也有点。没有覆盖的 bin 不能纳入同码率胜负。

### PSNR frontier 与多指标 Pareto

PSNR frontier cardinality：

| codec | 平均 PSNR frontier 点数 | 平均 frontier fraction |
|---|---:|---:|
| Nikon #826 | 8.75 | 0.375 |
| Sony #824 | 13.58 | 0.266 |

多指标 Pareto，指标为 `min(actual_bpp), max(PSNR), min(MAE), min(MAX)`：

| codec | 平均 Pareto 点数 | 平均 Pareto fraction |
|---|---:|---:|
| Nikon #826 | 14.38 | 0.598 |
| Sony #824 | 18.71 | 0.367 |

解读：Sony 的 PSNR 上包络更长，说明它在当前枚举空间里有更多 PSNR-relevant operating points；Nikon 的 Pareto fraction 更高，说明较多点在“rate/PSNR/MAE/MAX”联合指标下不被支配。这个差异本身就是前沿评价的核心：best policy 取决于 metric，不能用一个 bpp 或一个 PSNR 数字压扁。

## 文献对标后的写法

### Rate-distortion theory

Shannon rate-distortion 理论定义理论下界 `R(D)`，不是说一个实际 codec 在某 bpp 上只有一个解。实际 codec 给出的是 operational RD set。工程 RDO 常写作：

```text
minimize D(theta) + lambda R(theta)
```

其中 `theta` 是 selector、GTLI、subband priority、precinct refinement、component allocation 等所有编码决策。只给定 `R` 或 bpp，没有给定 `theta`，所以解通常不唯一。

### EBCOT/PCRD 与离散 rate control

JPEG 2000/EBCOT 的 PCRD 在多个 code-block truncation points 上做 R-D 选择。离散截断点通常无法精确命中目标 rate，只能在约束附近选择 operational point。当前 #824 的 selector/packet 平台和 #826 的 GTLI/Bp/Br 台阶，与这种离散 rate-control 结构是同一类数学现象。

### JPEG XS gains/priorities

JPEG XS 文献直接把 subband gains/priorities 当作可优化对象；不同 gains/priorities 可在相同 bpp 附近优化 PSNR、MS-SSIM 或任务指标。Brummer 与 de Vleeschouwer 的 JPEG XS 工作还把 1、3、5 bpp 下的优化并行到所有 CPU threads。这和我们现在讨论的 bpp 非唯一性完全一致：同一个空间预算下，bits 如何分配才是核心。

### BD-rate 与统计边界

Bjøntegaard Delta 是 codec 比较常用摘要，但它只在共同质量区间有意义，并且会受插值方式、曲线交叉和少量场景支配。新脚本因此保留 per-scene skip reason，并对 median/mean 做 bootstrap CI，不把单个中位数当成绝对胜负。

### Rate-distortion-perception

Blau/Michaeli 的 rate-distortion-perception tradeoff 和 LPIPS/JPEG AI 评价体系都提醒：sample-domain PSNR 不是感知质量的终点。RAW/CFA 评价尤其还缺 demosaic/ISP、显示映射和观察者协议。因此本文可以说“当前 RAW sample-domain operational envelope 的数学证据增强了”，但不能说“最终主观画质已经判完”。

## 仍未达到的前沿边界

1. 没有真实 Sony/Nikon production encoder 的可控多码率输出。
2. 没有完整 ARW/NEF 容器级编码，只是 decoder-visible syntax/math 和 minimal core bitstream closure。
3. 没有统一 demosaic/ISP/display pipeline 后的 LPIPS/DISTS/VMAF 或 MOS/2AFC 主观实验。
4. 没有编码复杂度、延迟、峰值内存、能耗、硬件吞吐的系统评估。
5. policy grid 不是厂商私有 RDO 搜索空间的穷举，只能给出 lower-bound evidence：bpp 非唯一、canonical 非唯一、upper envelope 与 Pareto 必须报告。

## 参考入口

- Shannon, *Coding Theorems for a Discrete Source With a Fidelity Criterion*: https://gwern.net/doc/cs/algorithm/information/1959-shannon.pdf
- Taubman, *High Performance Scalable Image Compression with EBCOT*: https://www.ee.nthu.edu.tw/~cwlin/courses/multimedia/notes/EBCOT.pdf
- JPEG XS overview: https://jpeg.org/jpegxs/index.html
- Brummer and de Vleeschouwer, *Adapting JPEG XS Gains and Priorities to Tasks and Contents*: https://openaccess.thecvf.com/content_CVPRW_2020/html/w7/Brummer_Adapting_JPEG_XS_Gains_and_Priorities_to_Tasks_and_Contents_CVPRW_2020_paper.html
- Blau and Michaeli, *The Rate-Distortion-Perception Tradeoff*: https://proceedings.mlr.press/v97/blau19a.html
- LPIPS paper: https://openaccess.thecvf.com/content_cvpr_2018/html/Zhang_The_Unreasonable_Effectiveness_CVPR_2018_paper.html
- JPEG AI metrics: https://jpegai.github.io/6-metrics/
