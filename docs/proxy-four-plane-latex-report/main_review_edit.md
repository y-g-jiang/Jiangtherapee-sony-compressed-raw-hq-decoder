# strict #824/#826 解码器可见编码器反推性能评价

同源 RAW、多指标、码流语法、BD-rate 与文献体系综合报告

作者：姜尧耕，Codex 协作实验记录  
日期：2026-06-03  
对应 LaTeX 源文件：`docs/proxy-four-plane-latex-report/main.tex`

> 编辑说明：这份 Markdown 用来快速改论文文字。它不替代 LaTeX 排版稿，重点是让摘要、结论、表格解释、边界声明和审稿风险先改顺。你可以直接在这里改，我再把改动合回 `main.tex`。

## 摘要

本文重写旧 11 页评价报告，但主数据替换为 strict #824/#826 编码器反推批次。评估对象不是相机厂商私有 production encoder，而是从 Sony #824 ARW6/LLVC3 与 Nikon #826 HE 解码器中可审计地反推出的 decoder-visible canonical encoder。

主实验目录为 `out/strict_824_826_math_eval_full_20260603`：24 个确定性同源合成 RGGB 场景、256x256 马赛克、三层变换、6 个目标请求 bpp、两条 canonical 路径、288 次编码、7488 条质量指标、288 条 syntax 记录和 48 条零量化 roundtrip 审计记录。`out/strict_824_826_math_insight_20260603` 进一步拆出 component/LUT 投影、变换 roundtrip、系数量化/反量化、VIF-style 信息保真、残差结构、高频误差能量、相位不均衡和局部 RD 斜率。新增 `out/production_fit_samples` 用公开 raw.pixls.us ARW/NEF 和本地负样本约束真实码流边界，`out/sony_stream_fitted_selector_eval_20260604` 用真实 Sony HQ selector 分布做敏感性实验。

结果显示，whole PSNR 的等质量 BD-rate 在可计算 11/24 个场景上为 Nikon #826 decoder-visible canonical 相对 Sony #824 decoder-visible canonical 中位 +4.758%。系数域 SNR 的等质量 BD-rate 在 11/24 个场景上为 +66.045%，VIF-style 信息保真的等质量 BD-rate 在 7/24 个场景上为 +41.731%；这两项只作为解释性指标，不作为 production encoder 证明。

同目标请求下，Nikon canonical 在 1.5、2.0、2.5、4.0 和 5.0 bpp 请求点的 whole PSNR 中位值更高；Sony canonical 在 3.0 bpp 点更高，并在 1.5 到 3.0 bpp 请求段更贴近目标码率。4.0 和 5.0 bpp 请求点上，Sony canonical 路径出现约 3.6946 bpp 的实际码率平台，因此这些高码率点不能按同实际码率比较。本文支持的结论是 decoder-visible canonical simulation 已形成可审计证据链；它不支持 production encoder equivalence claim，也不支持把任一真实相机编码器判定为无条件胜者。

真实码流锚点进一步收紧了边界：公开 Sony A7M5 HQ 样张为 4.468/4.762 bpp，packet type 分布固定为 `{"1":4,"3":9}`，selector mean 约 0.742；Nikon Z8 HE low 为 3.000 bpp/Bp=4，属于 #826 支持路径；Z8 5.000 bpp/Bp=2 属于当前 #826 unsupported HE*。这些事实不能证明 production encoder 等价，但能证明哪些 canonical target 点离真实码流近、哪些只是数学操作点。

关键词：RAW compression, RGGB, Bayer, LibRaw, Sony ARW6, Nikon HE, BD-rate, SSIM, MS-SSIM, GMSD, VIF-style, RD slope, stage-separated evaluation, decoder-visible canonical simulation

## 结论先行

本稿把“大报告”的评价框架和 strict 数据源合并在一起。旧大报告留下的是方法骨架：同源输入、多目标码率、率失真曲线、BD-rate、结构质量、ROI、码流语法、roundtrip 和证据边界必须一起出现。本文保留这套骨架，主数据改用 `out/strict_824_826_math_eval_full_20260603` 与 `out/strict_824_826_encoder_reversibility/audit.json`，不再引用旧代理实验作为结论来源。

按 BD-rate 的机器定义，`codec_a` 为 Nikon canonical，`codec_b` 为 Sony canonical。正值表示 Nikon canonical 为达到同等质量需要更多实际 syntax bpp。whole PSNR 的中位 BD-rate 为 +4.758%，但只有 11/24 个场景存在共同质量区间，分位区间也很宽。这不是“Sony 真实编码器已经胜出”的证据，只能说明 strict canonical 条件下可积分子集的等质量率失真方向。

另一张表回答的是同目标请求问题：Nikon canonical 在 1.5、2.0、2.5、4.0、5.0 bpp 请求点的 whole PSNR 中位值高于 Sony canonical，Sony canonical 在 3.0 bpp 点更高。等质量和同请求是两件事。不能互相替代。

本文比较两条路径。Sony 路径使用解码器可见的 LUT code domain、final-green 与 R/B residual 关系、packet selector、adaptive width、zero-run 和 sign syntax。Nikon 路径使用 sample14 LUT、step1/step2 Bayer 关系、CDF 5/3 系数、GTLI/GCLI、bit-plane magnitude 和 sign syntax。本文评价的是这些可见结构能反推出的 canonical encoder，而不是厂商相机内部的 RD 搜索和模式选择。

图：strict #824/#826 decoder-visible canonical encoder 结构

![[fig_strict_structure.png]]

## 证据等级

| 等级 | 状态与含义 |
|---|---|
| L1 | 未达到。没有厂商 production encoder，不能证明真实 Sony/Nikon 相机编码器的最终胜负。 |
| L2 | 达到。strict canonical encoder 使用同源合成 RAW、多目标请求和实际 syntax bpp。 |
| L3 | 达到。语法项来自 decoder-visible packet、selector、GTLI/GCLI、LUT 和 sign/data 关系。 |
| L4 | 部分达到。roundtrip、manifest、CSV、BD-rate、stage-separated insight eval、metric reference validation 和 minimal core bitstream closure 可复现；PDF 页数检查依赖外部 `pdfinfo`，外部 LibRaw 源码快照仍需随 artifact 一并固定。 |
| 边界 | 允许 decoder-visible canonical simulation；禁止 production encoder equivalence claim。 |

这种等级划分与 codec 文献中的基本习惯一致：单一 PSNR 或单一文件大小不能代表编码器性能；同源输入、多码率曲线、BD-rate、感知结构指标、信息保真指标、局部 RD 斜率和复杂度/语法成本需要同时报告。本文还额外要求列出不可由解码器唯一决定的 encoder RD policy。

## strict 反推审计

`audit.json` 对 Sony #824 和 Nikon #826 的可反推项做了逐项分类。结果是 exact reverse 8 项、canonical choice 2 项、not decoder determined 2 项。exact reverse 表示解码器已显式暴露语法或逆数学关系；canonical choice 表示可以构造合法 canonical 写法，但真实相机选择仍需验证；not decoder determined 表示解码器不可能唯一推出厂商 RD 搜索策略。

| 审计项 | 数量或状态 |
|---|---:|
| exact reverse | 8 |
| canonical choice | 2 |
| not decoder determined | 2 |
| 允许 decoder-visible canonical simulation | true |
| 允许 production encoder equivalence claim | false |

Sony 路径可反推 stream/tile directory、packet record、selector、adaptive width、zero-run、4-lane magnitude/sign、hierarchical synthesis 和 final green/RB relation。Nikon 路径可反推 precinct header、Bp/Br/Dpb/LB sizes、GCLI/GTLI、coefficient magnitude/sign bit-plane、dequantization、tile orchestration、step1/step2 Bayer reconstruction 和 sample14 LUT 投影。两者都不能由解码器推出真实相机的目标码率控制策略。

## 真实码流锚点

`tools/probe_production_fit_samples.py --jobs 15` 并行探测 11 个 ARW/NEF 输入：raw.pixls.us 的 Sony A7M5 full HQ、A7M5 APS-C HQ、Nikon Z8 HE low、Nikon Z8 HE high/HE*，以及本地 Downloads 里的 7 个 Z7 II NEF 负样本。输出在 `out/production_fit_samples`。

| 样张或类别 | bpp | 控制量 | 解释 |
|---|---:|---|---|
| Sony A7M5 full HQ | 4.468 | 13 packets, selector mean 0.742 | 真实 HQ full-frame single stream |
| Sony A7M5 APS-C HQ | 4.762 | 13 packets, selector mean 0.742 | 真实 HQ crop single stream |
| Nikon Z8 HE low | 3.000 | Bp=4 | 当前 #826 支持路径锚点 |
| Nikon Z8 HE high/HE* | 5.000 | Bp=2 | 当前 #826 unsupported 边界 |
| Z7 II 本地 NEF | 14.008 | no JPEG-XS marker | 负样本，不能误进 HE |

`production_fit_policy_audit.csv` 显示：Nikon 3.0 target 的实际 3.071 bpp 接近 Z8 HE Bp=4；Nikon 5.0 target 的实际 4.768 bpp 接近 Z8 5 bpp/Bp=2，但它是 HE* unsupported；Sony strict canonical 的 4.0/5.0 target 平台仍只有约 3.695 bpp，离下载的 HQ 样张至少 0.773 bpp。

当前审计文件记录了外部源码 SHA256，但仅有 hash 还不够。正式 artifact 需要同时固定对应源码快照、patch 或 commit 引用，并让 verifier 在当前工作目录下验证这些文件。否则换机后只能确认本文使用过某组 hash，不能确认审稿人手里的源码就是同一组文件。

## 指标与最小码流闭环

`metric_reference_validation.json` 用确定性测试向量检查评价指标实现。该 gate 对 PSNR/MSE/MAE/MAX 使用闭式公式 oracle，对 GMSD 使用独立 Prewitt/GMSD 公式 oracle，并把 SSIM/MS-SSIM 的 SciPy 卷积路径与脚本内置 no-SciPy fallback 交叉比较；输出记录文献来源、峰值范围、测试用例、容差和全部误差。当前结果为 3 个 case、24 个 check 全部通过，最大绝对差异约 8e-6。

`bitstream_closure.json` 给出 minimal core bitstream closure：脚本实际写出 Sony #824 LLVC3 最小 stream header、directory 和 type-1 packet records，以及 Nikon #826 HE 最小 precinct header、LB0 GCLI/data/sign substreams；随后用对应 decoder-visible parser/entropy/dequant 逻辑回读，并逐项比较系数、GCLI、bit-plane、sign 和 dequantization 结果。该闭环表明核心语法 bytes 可写可读，但不声称已经生成完整 Sony ARW 或 Nikon NEF 容器，也不允许 production encoder equivalence claim。

## 实验设置

| 项目 | 数值 |
|---|---|
| 主目录 | `out/strict_824_826_math_eval_full_20260603` |
| 输入 | 24 个确定性合成 RGGB 场景 |
| RAW 尺寸 | 256x256 马赛克，即四个 128x128 相位平面 |
| 目标请求 bpp | 1.5、2.0、2.5、3.0、4.0、5.0 |
| 变换层数 | 3 |
| 随机种子 | 20260603 |
| 编码行数 | 288 |
| 质量指标行数 | 7488 |
| syntax 行数 | 288 |
| roundtrip 行数 | 48 |
| 分层/洞见行数 | 3504 stage，3168 insight |
| Nikon LUT | `sample14`，`kMidpointScaleTable` |

场景覆盖 smooth gradient、fine texture、color edges、highlight rolloff、shadow noise、green phase alias、decorrelated color、slanted edge、thin black lines、zone plate、nyquist checker、micro contrast、random foliage、color checker、specular grid、shadow fabric、chroma noise、bayer phase steps、tile boundary stress、skin-like smooth、low contrast detail、high ISO texture、red/blue fine text 和 blue-channel detail。

## 评价指标

| 项目 | 用途 | 本文证据 |
|---|---|---|
| 实际 bpp 与目标请求 | 区分 rate-control 行为和实际 syntax 成本，避免只看 requested target。 | `rate_summary.csv` |
| PSNR/MSE | RAW sample 域均方误差评价，峰值范围使用黑电平 512 到白电平 16383。 | `metrics.csv` |
| MAE/MAX | 平均绝对误差和最坏点风险，适合检查近无损和局部离群。 | `metrics.csv` |
| R/G0/G1/B 分相位 | 防止 whole 指标掩盖 Bayer 相位、红蓝残差或绿相位风险。 | `bd_rate_psnr.csv` |
| grad-PSNR/Laplacian MAE | 检查边缘、纹理、高频和局部结构误差。 | 目标摘要 |
| SSIM/MS-SSIM/GMSD | 结构相似性与梯度相似性，避免只依赖 sample 域误差。 | `metric_reference_validation.json` |
| VIF-style 信息保真 | 以 Sheikh-Bovik 信息保真思想为 RAW plane 上的透明代理实现，检查视觉信息通道。 | `insight_metrics.csv` |
| 变换能量集中 | 报告 LL 能量占比、LL/detail 能量比和系数绝对值 Gini。 | `stage_metrics.csv` |
| 系数量化/反量化 | 报告 coeff SNR、coeff MAE、高频误差占比、零系数占比和 nonzero group 占比。 | `stage_summary.csv` |
| 局部 RD 斜率 | 用相邻实际 bpp 的有限差分报告 MSE drop、PSNR/SSIM/VIF gain 和 GMSD drop。 | `rd_slope_summary.csv` |
| BD-rate | 在共同质量区间积分，报告可计算样本数与分位区间。 | `bd_rate_*.csv` |
| syntax 组成 | 检查 packet/header、selector、width、zero-run、GCLI、data、sign 等成本。 | `syntax_summary.csv` |

RAW PSNR 定义为 `10 * log10((W - B)^2 / MSE)`，其中 `B=512`、`W=16383`。BD-rate 的机器方向固定为 Nikon canonical 相对 Sony canonical，正值代表 Nikon canonical 需要更多实际 syntax bpp。

## roundtrip 闭合

首先检查零量化或投影条件下的反向路径闭合。这点我并不将他并入精度考量，后面有更详细分析。这里我只为证明我采取的办法是完全闭合自洽的。

Nikon 路径的最大 roundtrip 残差为 1 DN，主要来自 sample14 LUT 的最近投影；Sony 路径最大 roundtrip 残差为 12 DN，主要来自任意 sample-domain 输入到 4096-entry Sony LUT code lattice 的最近邻投影。顺提一句这不同于 #824 PR 中已归因的高光 R/B predictor clamp 顺序问题，但二者都暴露了 Sony code-domain/LUT 边界在高光附近的敏感性。

| 路径 | median MAE | max abs |
|---|---:|---:|
| Sony canonical | 2.815 | 12 |
| Nikon canonical | 0.000153 | 1 |

图：零量化 roundtrip 投影误差

![[fig_strict_roundtrip.png]]

## 变换与量化分层评价

为尽可能仔细评估，我把编码链拆成三层：component/LUT 投影、CDF 5/3 变换 roundtrip、系数域量化/反量化。这防止把 LUT 投影、可逆变换数值误差和真正的系数量化误差混在一起。

变换 roundtrip 的中位 MAE 与 MAX 分别为：Sony canonical 2.815 DN、10 DN，Nikon canonical 0.000153 DN、1 DN；这与零量化 roundtrip 一致，说明主要差异仍来自 LUT/code-domain 投影而不是 CDF 5/3 变换本身。

能量集中方面，Sony canonical 的 LL 能量中位占比为 0.956，Nikon canonical 为 0.862；LL/detail 能量比中位分别为 13.43 dB 与 7.95 dB。这说明当前 canonical 分量选择下 Sony 路径更容易把能量集中到低频系数，但这不是完整 encoder RD policy 的证明。

系数量化/反量化层面，2.5 bpp 请求点的 coeff SNR 中位为 Sony 44.014 dB、Nikon 35.715 dB；3.0 bpp 点为 46.781 dB、36.802 dB；5.0 bpp 点为 49.103 dB、44.835 dB。coeff SNR 的等质量 BD-rate 在 11/24 个场景上为 Nikon canonical 相对 Sony canonical +66.045%。这只能说明当前 decoder-visible canonical 量化格点下 Sony 的系数域误差更小；Nikon 在若干 sample-domain PSNR 点仍然更高，说明变换、LUT、语法台阶和重建域误差不能互相替代。

图：变换与量化分层评价

![[fig_strict_stage_separation.png]]

## 同目标请求结果

同目标请求不是同实际码率。Sony canonical 在 1.5、2.0、2.5 和 3.0 bpp 请求点几乎贴着目标值；4.0 和 5.0 bpp 请求点则停在约 3.6946 bpp。相反，Nikon canonical 在 4.0 和 5.0 bpp 请求点更接近目标，但 1.5 bpp 点明显超码率。所以下表只能解释 requested-target 行为，不能单独当成 same-rate 结论。

| target | Sony bpp | Nikon bpp | Sony PSNR | Nikon PSNR | PSNR diff | MAE diff |
|---:|---:|---:|---:|---:|---:|---:|
| 1.5 | 1.4998 | 2.0060 | 52.736 | 55.777 | -3.041 | +5.376 |
| 2.0 | 1.9988 | 2.1353 | 57.443 | 60.381 | -2.938 | +2.321 |
| 2.5 | 2.4991 | 2.6151 | 60.848 | 61.868 | -1.020 | -0.209 |
| 3.0 | 2.9982 | 3.0708 | 64.015 | 63.206 | +0.809 | -1.825 |
| 4.0 | 3.6946 | 3.9351 | 66.767 | 68.325 | -1.558 | +0.469 |
| 5.0 | 3.6946 | 4.7676 | 67.398 | 70.104 | -2.706 | +0.935 |

PSNR diff 和 MAE diff 均为 Sony 减 Nikon。负 PSNR diff 表示 Nikon 中位 PSNR 更高；正 MAE diff 表示 Sony 中位 MAE 更大。3.0 bpp 是例外点，Sony 在 PSNR、MAE、grad-PSNR 和结构指标上同时更有利。

图：同目标请求下的中位差值

![[fig_strict_same_target_summary.png]]

## 结构指标矩阵

PSNR、grad-PSNR、MAE、SSIM、MS-SSIM 和 GMSD 的同目标中位差不能硬合成一个分数。SSIM/MS-SSIM 在 2.5 和 3.0 bpp 点偏向 Sony，在 4.0 和 5.0 bpp 点偏向 Nikon；GMSD 在 3.0 bpp 点偏向 Sony，在若干其他点偏向 Nikon。

这类指标差异说明，单一 PSNR 结论不够。对于 RAW 编码器反推评价，PSNR 可以作为主轴，但需要 MAE/MAX、结构相似性、梯度指标和 ROI 共同约束。

图：多指标中位差矩阵

![[fig_strict_metric_matrix.png]]

## 数学洞见指标

VIF-style 指标借鉴 information fidelity/VIF 的高斯通道思想，但本文只把它实现为 RAW plane 上透明、可复现的解释性代理；它不等同于原论文完整 MATLAB VIF，也不替代 sample-domain 正确性。

在 2.5、3.0 和 5.0 bpp 请求点，VIF-style 信息保真中位分别为 Sony 0.999037/0.999541/0.999738，Nikon 0.994117/0.994699/0.997051。按同质量积分，VIF-style BD-rate 只有 7/24 个场景存在共同质量区间，中位为 +41.731%。这说明该指标偏向 Sony canonical，但可计算覆盖较少，证据强度低于主 sample-domain 曲线。

另一方面，高频误差能量占比在同三点上 Sony 明显高于 Nikon，说明 Sony 的误差更容易进入高频残差；Nikon 的残差邻域相关更高，说明误差更平滑或更结构化。这些方向差异正是 rate-distortion-perception 边界提醒的内容：不同数学投影回答不同问题，不能被合成成一个无条件胜负。

局部 RD 斜率报告相邻实际 bpp 点的边际收益。Sony 的中位 MSE drop、PSNR gain、VIF gain 和 GMSD drop 分别为 357.607、5.559、0.001839 和 0.000305；Nikon 分别为 114.781、4.134、0.000707 和 0.000117。由于 Nikon canonical 的 GTLI/GCLI row 是离散台阶，局部斜率段数少于 Sony；这些数值只能说明当前 canonical sweep 的边际收益形状，不能反推出厂商真实 Lagrangian lambda。

图：数学洞见指标矩阵

![[fig_strict_insight_metrics.png]]

图：局部 RD 斜率

![[fig_strict_rd_slope.png]]

## 码流语法与实际 bpp

Sony canonical 路径用 continuous base step 搜索，1.5 到 3.0 bpp 请求段的实际 bpp 非常贴近目标；到了 4.0 和 5.0 bpp，请求已经高于当前搜索/语法组合能给出的平台。Nikon canonical 路径由有限 GTLI/GCLI 行和 Bp/Br 组合决定，实际 bpp 呈台阶状。这个行为不是实现缺陷，而是 strict 反推模型的控制空间边界；公平比较时必须把 same-target、same-actual-bpp 和可达码率范围分开报告。

在 2.5 bpp 请求点，Sony canonical 的实际 bpp 中位为 2.499，Nikon canonical 为 2.615。Nikon 的 header 中位约 0.009 bpp，主要成本来自 GCLI、data 和 sign 子流；Sony 的 payload、width update、sign 和 zero-run 决定主要成本。报告性能时必须使用实际 bpp，而不是只引用 target bpp。高码率端还需要单独标出 Sony 平台化，否则 5.0 bpp 请求点会被误读为同码率比较。

图：strict syntax 成本

![[fig_strict_syntax_summary.png]]

## Sony selector stream-fit 敏感性

真实 Sony HQ probe 的 selector mean 约 0.742，而 strict canonical 的 Sony component selector mean 为 1.25。为检查这个选择是否过于随意，我新增 `tools/stream_fitted_sony_selector_eval.py --jobs 15`：只把 Sony selector 分布替换为真实 probe 的低差异交织周期，其他 transform、base-step 搜索、width/zero-run/sign 成本、场景、target 和指标都保持不变。

| target | strict bpp | fit bpp | strict PSNR | fit PSNR |
|---:|---:|---:|---:|---:|
| 1.5 | 1.500 | 1.500 | 52.736 | 51.575 |
| 2.0 | 1.999 | 2.000 | 57.443 | 55.819 |
| 2.5 | 2.499 | 2.499 | 60.848 | 59.346 |
| 3.0 | 2.998 | 2.999 | 64.015 | 62.252 |
| 4.0 | 3.695 | 3.997 | 66.767 | 66.637 |
| 5.0 | 3.695 | 4.180 | 67.398 | 67.653 |

这个结果说明：贴近真实 selector 分布后，Sony 高码率可达范围从约 3.695 bpp 抬到约 4.180 bpp，但仍低于公开 HQ 样张的 4.468/4.762 bpp；低中码率 PSNR 下降，高码率接近或略高。所以它是敏感性和调参方向，不是新的 production encoder claim。

## 等质量 BD-rate

只有同一 source 上两条 RD 曲线存在共同质量区间时才计入 `ok_sources`。这也是为什么 whole PSNR 只有 11/24 个场景可计算，而不是 24/24。

| 指标 | group | ok/skipped | 中位 BD-rate |
|---|---|---:|---:|
| PSNR | whole | 11/13 | +4.758% |
| MAE | whole | 11/13 | +13.614% |
| grad-PSNR | detail | 9/15 | +0.828% |
| SSIM | detail | 11/13 | +1.416% |
| MS-SSIM | detail | 11/13 | +5.598% |
| GMSD | detail | 11/13 | +0.237% |
| VIF-style | information | 7/17 | +41.731% |
| coeff SNR | quantization | 11/13 | +66.045% |

whole PSNR 的分位区间为 -29.635% 到 +46.572%，MAE 为 -21.790% 到 +58.796%。这个区间横跨两种方向，说明中位数只能作为可计算子集上的摘要。VIF-style 只有 7/24 个场景可积分，error-HF 类指标覆盖更少，系数域指标又不是同一物理系数空间下的生产编码器比较。本文因此把 BD-rate 放在证据链中使用，不把它写成唯一裁判。后续版本还应加入单调包络、PCHIP/梯形积分敏感性、skip reason 和 per-scene sign count。

图：等质量 BD-rate 摘要

![[fig_strict_bd_rate_summary.png]]

## 场景级差异

按 6 个 target 点的 whole PSNR 平均差排序，负值偏向 Nikon，正值偏向 Sony。Sony 更容易在相位别名、棋盘、色度噪声和部分高频纹理场景中占优；Nikon 更容易在平滑、暗部、亮部 rolloff、低对比细节和若干红蓝细节场景中占优。场景排序解释了为什么 BD-rate 的可计算样本数和分位区间必须报告。

这类排序也提醒我们，RAW 压缩评价不能只选少数漂亮样片。需要包含暗部噪声、亮部滚降、绿色相位、红蓝分量、细线、棋盘、纹理和低对比细节，否则结论会对场景分布过拟合。

图：场景级 whole PSNR 方向

![[fig_strict_scene_rank.png]]

## 局部 ROI 检查

ROI 不是为了主观挑图，而是为了检查全局曲线没有掩盖局部风险。当前报告使用 2.0 bpp 请求点的 strict canonical 重建，显示参考、Nikon、Sony 以及两者误差热图。

图：暗部噪声 ROI，shadow_noise，2.0 bpp 请求

![[fig_strict_roi_shadow_noise.png]]

图：高 ISO 纹理 ROI，high_iso_texture，2.0 bpp 请求

![[fig_strict_roi_high_iso_texture.png]]

图：亮部 rolloff ROI，highlight_rolloff，2.0 bpp 请求

![[fig_strict_roi_highlight_rolloff.png]]

图：细线 ROI，thin_black_lines，2.0 bpp 请求

![[fig_strict_roi_thin_black_lines.png]]

图：红蓝细节 ROI，red_blue_fine_text，2.0 bpp 请求

![[fig_strict_roi_red_blue_fine_text.png]]

## 可复现性

所有主数字来自当前脚本和 strict 目录，报告不引用旧代理输出作为主结论。当前 Windows 环境下应优先使用 `py` 或显式 Python 路径运行脚本；直接调用 `python` 可能落到 WindowsApps shim。

| 阶段 | 命令或文件 |
|---|---|
| 反推审计 | `audit_824_826_encoder_reversibility.py` |
| strict 编码评估 | `strict_824_826_math_eval.py --jobs 24` |
| 数学洞见与分层评价 | `strict_824_826_insight_eval.py --jobs 24`，输出 `out/strict_824_826_math_insight_20260603` |
| 真实样张并行探测 | `probe_production_fit_samples.py --jobs 15`，输出 `out/production_fit_samples` |
| production-fit audit | `audit_production_fit_policy.py` |
| Sony selector 敏感性 | `stream_fitted_sony_selector_eval.py --jobs 15`，输出 `out/sony_stream_fitted_selector_eval_20260604` |
| 指标验证 | 指标 reference validation 脚本，输出 `metric_reference_validation.json` |
| 最小码流闭环 | #824/#826 minimal bitstream closure 脚本，输出 `bitstream_closure.json` |
| BD-rate | `compute_bd_rate.py`，覆盖 PSNR、MAE、grad-PSNR、SSIM、MS-SSIM、GMSD、VIF-style 和 coeff SNR |
| 机器摘要 | `summarize_strict_824_826_math_eval.py` |
| 图表 | strict 图表脚本 |
| 报告审计 | `py tools/verify_proxy_four_plane_final.py`，或显式 `C:\Python314\python.exe` |

核心文件包括 `manifest.json`、`encodes.csv`、`metrics.csv`、`syntax_summary.csv`、`roundtrip_audit.csv`、`target_request_summary.csv`、`rate_summary.csv`、`bd_rate_*.csv`、`stage_metrics.csv`、`insight_metrics.csv`、`rd_slope_summary.csv`、`combined_big_comparison.csv`、`metric_reference_validation.json`、`bitstream_closure.json`、`out/production_fit_samples/real_bitstream_controls.csv`、`out/production_fit_samples/production_fit_policy_audit.csv`、`out/sony_stream_fitted_selector_eval_20260604/rate_summary.csv` 和 `paper_numbers.json`。其中 `paper_numbers.json` 是主质量数字的机器摘要入口，`combined_big_comparison.csv` 是分层与洞见评价的合并入口。

完整 artifact 仍有两项审稿风险。第一，`audit.json` 和 `bitstream_closure.json` 中的外部源码路径需要替换为仓库内快照、可访问 patch 或明确 commit。第二，PDF verifier 若启用 `--require-pdf`，需要 `pdfinfo` 或等价的内置页数检查；否则无 PDF 工具的机器会把环境问题报告成失败。

## 边界与限制

第一，本文没有真实 production encoder。strict canonical encoder 是从解码器可见数学关系和语法中反推出的可审计对象，但不等同于 Sony 或 Nikon 相机固件。

第二，目标请求 bpp 与实际 syntax bpp 必须分开，尤其 Sony 在高码率端的平台化和 Nikon 有限 GTLI/GCLI row 的台阶都会影响同目标表的解释。

第三，真实码流锚点只能约束解释边界：Sony HQ probe 说明公开 HQ 文件在 4.468/4.762 bpp 且 selector mean 约 0.742；Nikon 5 bpp/Bp=2 是 HE* unsupported 边界，不能和当前 #826 HE 支持路径混为一谈。

第四，BD-rate 可计算样本只有 11/24 或 9/24，部分解释性指标更少；中位数不能写成全场景真理。

第五，合成 corpus 用来压力测试结构假设，不等于真实相机拍摄分布。

第六，ROI 只能辅助解释局部风险，不能替代整套曲线。

第七，VIF-style、残差结构、高频误差能量、局部 RD 斜率和 rate-distortion-perception 讨论只增强解释层，不把 RAW Bayer sample-domain 正确性降级为次要目标。LPIPS、VMAF、MOS 等深度感知或视频/主观融合指标在文献中有价值，但当前报告没有 demosaic/ISP 统一管线和观察者协议，因此不把它们作为主数值结论。

第八，当前 artifact 还需要固定外部源码快照并消除 `pdfinfo`/`python` shim 等环境脆弱点，才能达到强 artifact review 的“一键复现”标准。

minimal core bitstream closure 只允许本文说：核心 packet/precinct 语法层已经写出 bytes，并由 decoder-visible parser/entropy/dequant 逻辑回读一致。本文仍然不允许说：已经写出完整 Sony ARW 或 Nikon NEF 相机容器，或者已经复原厂商私有 RD 搜索、目标码率控制与模式选择。

因此本文允许的表述是：在当前 strict decoder-visible canonical simulation 上，Sony canonical 与 Nikon canonical 的率失真结果已经可复现、可审计，并呈现出按码率点、指标和场景变化的优势分布。本文不允许的表述是：已经证明真实 Sony/Nikon production encoder 的 production encoder equivalence claim，或已经判定某个真实相机编码器无条件优于另一个。

## 结语

本文不再复述旧代理结论，而是把 strict #824/#826 编码器反推结果放进同一套评价框架中。主数据来自 strict 批次；变换、量化、信息保真、残差结构和局部 RD 斜率先分层运行，再合并比较。

Nikon canonical 在多个同目标请求 PSNR 点、roundtrip 投影精度和部分平滑/暗部/高亮场景上更强。Sony canonical 在 1.5 到 3.0 bpp 请求段的码率贴合、3.0 bpp 点、系数域量化 SNR、VIF-style 信息保真、若干纹理/相位压力场景以及可积分 BD-rate 中位方向上更强；4.0 和 5.0 bpp 请求点则必须把 Sony 码率平台化写清楚。

本文能给出的稳健结论是：decoder-visible canonical 层面的比较已经形成可复现证据链，production encoder 层面的最终胜负仍需真实同源多码率编码器输出。

## 参考文献占位

1. B. E. Bayer, "Color imaging array," U.S. Patent 3,971,065, 1976.
2. ITU-T Recommendation J.340, "Reference algorithm for peak signal-to-noise ratio," 2023.
3. ISO/IEC 14495-1, "JPEG-LS lossless and near-lossless image coding," 1999.
4. G. J. Sullivan and T. Wiegand, "Rate-distortion optimization for video compression," IEEE Signal Processing Magazine, 1998.
5. Z. Wang et al., "Image quality assessment: from error visibility to structural similarity," IEEE TIP, 2004.
6. Z. Wang, E. P. Simoncelli, and A. C. Bovik, "Multiscale structural similarity for image quality assessment," ACSSC, 2003.
7. W. Xue, L. Zhang, X. Mou, and A. C. Bovik, "Gradient magnitude similarity deviation," IEEE TIP, 2014.
8. H. R. Sheikh and A. C. Bovik, "Image information and visual quality," IEEE TIP, 2006.
9. G. Bjontegaard, "Calculation of average PSNR differences between RD-curves," VCEG-M33, 2001.
10. C. Herglotz et al., "The Bjontegaard Bible: why your way of comparing video codecs may be wrong," arXiv:2304.12852, 2023.
11. Y. Blau and T. Michaeli, "The rate-distortion-perception tradeoff," ICML/PMLR, 2019.
12. R. Zhang et al., "The unreasonable effectiveness of deep features as a perceptual metric," CVPR, 2018.
13. Z. Li et al., "Toward a practical perceptual video quality metric," Netflix Technology Blog/VMAF technical report, 2016.
