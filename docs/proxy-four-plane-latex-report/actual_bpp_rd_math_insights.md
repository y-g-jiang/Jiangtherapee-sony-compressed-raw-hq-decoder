# actual bpp 横轴、单调性与数学 insight 补充材料

## 先回答 3 bpp / 4 bpp 的表观反转

原 target 表里的现象是：

| requested target | Sony median actual bpp | Sony median PSNR | Nikon median actual bpp | Nikon median PSNR | Sony - Nikon |
|---:|---:|---:|---:|---:|---:|
| 3.0 | 2.998 | 64.015 dB | 3.071 | 63.206 dB | +0.809 dB |
| 4.0 | 3.695 | 66.767 dB | 3.935 | 68.325 dB | -1.558 dB |
| 5.0 | 3.695 | 67.398 dB | 4.768 | 70.104 dB | -2.706 dB |

这不是“同 actual bpp 下 4 bpp Nikon 突然大幅反超”的证据。3.0 target 时两者实际码率接近；4.0 target 时 Nikon median actual bpp 比 Sony 高约 0.240 bpp，且 Nikon 走到另一组 GTLI/Bp/Br 台阶，而 Sony canonical 已明显靠近高码率平台。5.0 target 更明显：Sony median actual bpp 仍约 3.695，Nikon 已到 4.768。

因此，所有 RD 图的横轴必须改成 `actual syntax bpp`。新增图：

- `out/bpp_policy_multiplicity_20260604/actual_bpp_rd_insights/fig_actual_bpp_canonical_median_rd.png`
- `out/bpp_policy_multiplicity_20260604/actual_bpp_rd_insights/fig_actual_bpp_canonical_scene_cloud_3_4.png`
- `out/bpp_policy_multiplicity_20260604/actual_bpp_rd_insights/fig_actual_bpp_policy_range_envelope.png`
- `out/bpp_policy_multiplicity_20260604/actual_bpp_rd_insights/fig_actual_bpp_policy_paired_delta.png`

生成命令：

```powershell
python tools\actual_bpp_rd_insights.py `
  --jobs 16 `
  --out-dir out\bpp_policy_multiplicity_20260604\actual_bpp_rd_insights `
  --bin-step 0.25 `
  --bin-tolerance 0.20
```

manifest 记录 `jobs_requested=16`、`cpu_count=16`。该脚本把 scene×codec 分组和 policy-bin 分组都放入 `ProcessPoolExecutor`，避免把 heavy sweep 之后的审计串行化。

## 单调性审计

脚本输出 `canonical_monotonic_audit.csv`。按每个 `scene × codec` 把 strict canonical 点按 actual bpp 排序后检查：

> 是否存在 actual bpp 增加但 whole-image raw PSNR 下降？

结果为 0 个违反点，覆盖 24 scenes × 2 codecs = 48 条轨迹。因此不能把 3/4 target 表读成“质量随 bpp 上升不单调”。更准确的说法是：

1. 对同一 strict canonical path，当前审计没有发现 actual-bpp 单调性破坏。
2. 3/4 bpp 的 winner flip 来自跨 codec operational RD 曲线交叉、target/actual mismatch 和离散 rate-control 台阶。
3. 若画的是 policy-bin summary，某些中位曲线可能不平滑，因为每个 bin 的 coverage scene set 不同；这类图只能解释“可达点与覆盖率”，不能单独当作严格单调曲线。

target 3 到 target 4 的同场景变化：

| codec | median actual-bpp increase | median PSNR increase | negative PSNR count |
|---|---:|---:|---:|
| Sony #824 strict canonical | +0.702 bpp | +3.023 dB | 0 |
| Nikon #826 strict canonical | +0.531 bpp | +0.483 dB | 0 |

## actual-bpp paired policy 上包络

为避免不同 bin 覆盖不同场景造成假波动，`policy_actual_bpp_paired_bin_comparison.csv` 只在同一个 actual-bpp bin 内、双方 codec 都有候选的同一批 scenes 上比较 best PSNR。方向为 Sony #824 best minus Nikon #826 best。

| actual bpp bin ±0.20 | paired scenes | median best delta | Sony/Nikon wins |
|---:|---:|---:|---:|
| 3.0 | 14/24 | +1.097 dB | 9/5 |
| 4.0 | 13/24 | +2.080 dB | 12/1 |
| 5.0 | 12/24 | +5.122 dB | 12/0 |

这张 paired actual-bpp 表与 requested-target 表方向不同，说明旧 target 横轴确实会误导。它仍不是 production encoder 结论，因为 policy grid 不等于厂商私有 RDO；但它足以证明：比较 3/4/5 bpp 时必须用 actual-bpp axis，并同时报告 paired coverage。

## 数学 insight：为什么 bpp 不是唯一解

### 1. Shannon RD 函数是理论下界，不是单一 encoder 点

Shannon 的 rate-distortion theory 定义的是在给定 distortion 约束下的最小等价 rate `R(d)`。这给出“rate 与 distortion 的理论边界”，但并不说明一个实际标准 codec 在某个 bpp 只有一个合法重建。实际 codec 只能产生有限或离散的 operational points；这些点由 transform、quantizer、context model、packet/precinct syntax、rate-control policy 决定。

报告里应避免说“bpp 决定质量”。更准确是：

> 对一个 source distribution 和 distortion measure，存在理论 RD 下界；对一个具体 codec 和 policy set，存在 operational RD set。实验测到的是后者，且同一个 rate 附近可能有多个 operational points。

### 2. Operational RD set 和 Lagrangian/Pareto frontier

Ortega 与 Ramchandran 的综述把实际编码中的资源分配写成 R-D 框架，并强调 admissible coding parameters 形成 operational R-D characteristic。工程上常解的是

```text
minimize   D(theta) + lambda R(theta)
```

其中 `theta` 是所有可调编码决策。若 `theta` 是离散集合，同一个 rate 附近可能有多个点：有些在 Pareto frontier 上，有些被支配，有些只在另一个 distortion metric 下有意义。因此单个 canonical 点最多是一条轨迹，不能代替 convex hull / Pareto frontier。

这正对应本实验：`selector_map`、`row_cycle`、`GTLI/GCLI bias`、`Bp/Br`、subband/component allocation 都是 `theta` 的一部分。给定 bpp 后还缺少 `theta`，所以解不唯一。

### 3. 离散 rate control 导致 target 不等于 actual

Taubman 的 EBCOT/PCRD 是最好的类比。EBCOT 每个 code-block 有 embedded bit-stream 和多个 truncation points；rate control 是在这些离散截断点上做 R-D 选择。论文明确指出，离散 truncation points 下通常无法找到刚好等于目标 rate 的阈值，只能找满足约束的近似点。这和当前 #826 的有限 GTLI/Bp/Br 台阶、#824 的高码率平台完全同构。

所以单调性应当按 actual bpp 和同一条可嵌套 refinement path 检查。requested target 只是 encoder 控制输入；它不应放在 RD 图横轴上当作真实 rate。

### 4. Wavelet/subband 编码天然是多维 bit allocation

Mallat 的多解析度/wavelet 分解把图像表示为 LL 与不同方向/尺度的 detail subbands。JPEG 2000/EBCOT、JPEG XS 和我们当前模拟的 Nikon/Sony 路径都不是在“一个标量 bpp”上直接量化整幅图，而是在 subband、precinct、component、bit-plane、significance map 等多维结构上分配 bits。

因此 bpp 是所有这些结构成本的总和：

```text
R_total = R_header + R_control + R_significance/GCLI + R_payload + R_sign
```

同一个 `R_total` 可以来自完全不同的分配向量。对 RAW/CFA 来说，green base、green phase、R/B residual、Bayer phase detail 的权重不同，sample-domain PSNR、grad-PSNR、MS-SSIM 和视觉噪声结构都会不同。

### 5. JPEG XS 的 gains/priorities 是直接证据

JPEG 官方文档说明 JPEG XS 面向低延迟、低复杂度、精确 bitrate control，并支持 raw Bayer。Brummer 与 de Vleeschouwer 进一步指出，JPEG XS 对每个 sub-band 有 gains 和 priorities；ISO 默认表偏向 PSNR，但其他值在特定场景可能更好，而且这些 weights 写入 header，兼容 decoder。论文还用 1、3、5 bpp 重复优化，并把优化并行到所有 CPU threads。

这对本报告非常关键：连 JPEG XS 自己的公开研究都把“同 bpp 下的 gain/priority policy”作为可优化对象。也就是说，bpp 非唯一不是我们的模拟怪现象，而是该类 codec 的正常数学结构。

### 6. 指标不唯一，最佳 policy 也不唯一

Blau 与 Michaeli 的 rate-distortion-perception tradeoff 提醒：低 distortion 不等于高 perceptual quality；压缩评价至少有 rate、distortion、perception 三方权衡。Brummer 的 JPEG XS 实验也显示，MS-SSIM weights、PSNR weights、AI-task weights 可以分别优化不同目标，而且互相不必兼容。

所以报告里不要只写 “best PSNR policy”。应当写：

1. `best_PSNR` 是 sample-domain MSE/PSNR 下的上包络。
2. `best_MS-SSIM`、`best_grad-PSNR`、`best_GMSD` 或视觉任务权重可能不同。
3. 当前 policy sweep 的 PSNR interval 是 bpp 非唯一性的下界证据，不是完整 perceptual frontier。

## 建议写进正文的判断句

可以直接加入：

> 本文所有 RD 图必须以 actual syntax bpp 为横轴。requested target bpp 只是 rate-control input；在有限 GTLI/Bp/Br、selector、precinct 和 packet policy 下，actual bpp 呈离散台阶或平台。3.0 与 4.0 target 上的 #824/#826 winner flip 不构成“质量随 bpp 上升不单调”的证据；按 scene×codec 的 actual-bpp 排序审计没有发现 PSNR 单调性违反。该现象应解释为 operational RD curve crossing 与 target/actual mismatch。

再接：

> 给定 actual bpp 仍不是唯一编码解。按照 Shannon/R-D 和 Lagrangian bit allocation 视角，实际 encoder 的每个 selector、GTLI、subband priority、precinct refinement 和 component allocation 都是编码决策变量。公平比较应报告 policy interval、paired actual-bpp coverage 和 Pareto/upper envelope，而不是把 canonical 单点解释为 production encoder optimum。

## 参考文献线索

- Shannon, C. E. (1959). *Coding Theorems for a Discrete Source With a Fidelity Criterion*. IRE International Convention Record. https://gwern.net/doc/cs/algorithm/information/1959-shannon.pdf
- Ortega, A., & Ramchandran, K. (1998). *Rate-Distortion Methods for Image and Video Compression*. IEEE Signal Processing Magazine. DOI: 10.1109/79.733495
- Shoham, Y., & Gersho, A. (1988). *Efficient bit allocation for an arbitrary set of quantizers*. IEEE Trans. Acoustics, Speech, and Signal Processing. DOI: 10.1109/29.90373
- Mallat, S. G. (1989). *A theory for multiresolution signal decomposition: the wavelet representation*. IEEE TPAMI. DOI: 10.1109/34.192463
- Shapiro, J. M. (1993). *Embedded Image Coding Using Zerotrees of Wavelet Coefficients*. IEEE Trans. Signal Processing. DOI: 10.1109/78.258085
- Said, A., & Pearlman, W. A. (1996). *A New, Fast, and Efficient Image Codec Based on Set Partitioning in Hierarchical Trees*. IEEE TCSVT. DOI: 10.1109/76.499834
- Taubman, D. (2000). *High Performance Scalable Image Compression with EBCOT*. IEEE Transactions on Image Processing. https://www.ee.nthu.edu.tw/~cwlin/courses/multimedia/notes/EBCOT.pdf
- JPEG Committee. *JPEG XS overview and documentation*. https://jpeg.org/jpegxs/index.html and https://jpeg.org/jpegxs/documentation.html
- Brummer, B., & de Vleeschouwer, C. (2020). *Adapting JPEG XS Gains and Priorities to Tasks and Contents*. CVPR Workshops. https://openaccess.thecvf.com/content_CVPRW_2020/html/w7/Brummer_Adapting_JPEG_XS_Gains_and_Priorities_to_Tasks_and_Contents_CVPRW_2020_paper.html
- Richter, T., Foessel, S., Descampe, A., & Rouvroy, G. (2021). *Bayer CFA Pattern Compression With JPEG XS*. IEEE Transactions on Image Processing. DOI: 10.1109/TIP.2021.3095421
- Blau, Y., & Michaeli, T. (2019). *Rethinking Lossy Compression: The Rate-Distortion-Perception Tradeoff*. ICML/PMLR. https://proceedings.mlr.press/v97/blau19a.html
