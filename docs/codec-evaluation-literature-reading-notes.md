# 图像编码器评价文献扩展阅读笔记

日期：2026-06-01  
用途：补充 `codec-paper-evaluation-matrix.md`。重点不是复述每篇论文的算法，而是抽取“他们如何评价一个编码器”，再翻译到 LibRaw #826 Nikon HE 与 #824 Sony ARW6 CRAW HQ 这种 RAW 解码器审查。

## 先把问题分清

编码器论文评价的是“给定原图和码率，编码器是否更好地保存了信息、感知质量和工程约束”。LibRaw #824/#826 评价的是“给定相机已经产生的私有压缩 bitstream，开源解码器是否忠实、安全、可复现地还原参考输出”。

因此，读论文后得到的排序是：

1. **Decoder conformance 先于感知质量**：对 RAW PR，逐像素/逐 CFA site/高光/边界差异比“预览图好看”更有证据力。
2. **Rate 仍要记录，但不是胜负主轴**：没有实现相机 encoder 时，不能做完整 RD 曲线；但 strip bpp、压缩比、样本间离群值仍能说明覆盖范围。
3. **PSNR/MS-SSIM/LPIPS 等指标要分层使用**：它们适合评价 lossy encoder 或 preview 辅助，不适合替代 reference decoder 比较。
4. **主观测试只服务 visually lossless 声明**：厂商说 HE/CRAW HQ 视觉无损，需要 BT.500/P.910/CLIC 类 human study；LibRaw PR 先要证明 decoder correctness。
5. **复杂度和安全性是现代 codec 的正式评价项**：speed、latency、memory、decoder size、bounds checks、corrupt-stream behavior 都应该进入 #824/#826 报告。

## 文献扩展矩阵

| # | 来源 | 他们怎么评价 encoder | 对 #824/#826 的可用结论 |
|---:|---|---|---|
| 51 | Blau & Michaeli, [The Rate-Distortion-Perception Tradeoff](https://arxiv.org/abs/1811.06683) | 明确 rate、distortion、perception 三者不可同时最优 | 不要把“感知好”误说成“数值正确”；RAW decoder 的 distortion/reference 一致性权重最高 |
| 52 | Patel et al., [Human Perceptual Evaluations for Image Compression](https://arxiv.org/abs/1908.04187) | 用人类偏好实验检验 PSNR/MS-SSIM 排名，指出高 MS-SSIM 可能误导 | PR 讨论里若说“看不出差异”，必须降级为辅助观察，不能替代 Adobe/NX/Sony reference diff |
| 53 | Mier et al., [Deep Perceptual Image Quality Assessment for Compression](https://arxiv.org/abs/2103.01114) | 构建压缩图像人类偏好数据集，训练 full-reference perceptual metric | 若未来做 demosaic preview 质量报告，可加 perceptual metric；当前 #824/#826 仍要先看 CFA-domain exactness |
| 54 | Jamil, [Review of Image Quality Assessment Methods for Compressed Images](https://www.mdpi.com/2313-433X/10/5/113), 2024 | 综述压缩 artifact、objective metrics、subjective assessment 和新标准 | 支持把指标分成 objective、perceptual、subjective 三层，避免混用术语 |
| 55 | CLIC 2021, [challenge tasks](https://archive.compression.cc/2021/tasks/index.html) | 固定 bpp 点，人类偏好决定优胜；objective metrics 只作 leaderboard 辅助 | #824/#826 应固定样本、参考版本、crop、统计字段；不要用单张图的主观感觉收尾 |
| 56 | CLIC 2025, [leaderboard fields](https://clic2025.compression.cc/leaderboard/video/test/) | 同时列 bitrate、PSNR、MOS、data size、decoder size、decoding time | LibRaw PR 评论可以模仿这种表：样本、raw size、strip bpp、decode time、reference diff、剩余 unsupported |
| 57 | CompressAI, [image model zoo](https://interdigitalinc.github.io/CompressAI/zoo.html) and [paper](https://arxiv.org/abs/2011.03029) | 统一模型、dataset、bpp、PSNR/MS-SSIM 与命令行评估 | 本仓库 `compare_raw_outputs.py` 要继续输出 JSON/CSV，使 #824/#826 的证据像 benchmark 一样可复现 |
| 58 | CompressAI, [CLI usage](https://interdigitalinc.github.io/CompressAI/cli_usage.html) | 提供 `eval_model` 与按目标 metric 搜索 quality 参数的命令 | 对 RAW decoder，则应提供一键 probe/compare 命令，而不是只保留手工笔记 |
| 59 | Yu et al., [Evaluating the Practicality of Learned Image Compression](https://arxiv.org/abs/2207.14524) | 不只看 PSNR/MS-SSIM，还看 GPU/CPU latency、throughput、工程优化 | #824 的 R6 tiled 与 M5 single-stream 都要记录 wall time 和内存上限，证明不是“能跑但不可用” |
| 60 | LoC-LIC, [Low Complexity Learned Image Coding](https://arxiv.org/abs/2504.21778) | 报告 RD 能力同时量化 MAC/Pixel 等计算复杂度 | Nikon #826 代码量大，不能只报能解；要补 allocation、precinct read、tile carry 的成本与安全审查 |
| 61 | Li et al., [HPCM, ICCV 2025](https://openaccess.thecvf.com/content/ICCV2025/html/Li_Learned_Image_Compression_with_Hierarchical_Progressive_Context_Modeling_ICCV_2025_paper.html) | 用 Kodak/CLIC/TestImages，RD 曲线、复杂度与 coding time 评估上下文模型 | 对 #826 的 GCLI predecessor/reset 与 #824 的 residual predictor，要按依赖状态定位错误，不只看最终图 |
| 62 | Wang et al., [Variable-rate U-SFRB Adapter](https://link.springer.com/article/10.1186/s13634-025-01268-x), 2025 | Kodak/CLIC/Tecnick/DIV2K 多数据集，PSNR、rate、CompressAI baseline | Sony/Nikon 样本矩阵必须覆盖机型、crop、场景复杂度；单一 bpp 或单一样本不能代表 codec |
| 63 | Cheng et al., [GMM likelihoods and attention](https://arxiv.org/abs/2001.01568) | Kodak/Tecnick/CLIC，PSNR/MS-SSIM，传统 codec baseline | 差异报告要分 dataset/场景；RAW 里对应为 A7M5/A7R6/Z8/Zf/Z9/Z6III 分组 |
| 64 | He et al., [Checkerboard Context Model](https://openaccess.thecvf.com/content/CVPR2021/html/He_Checkerboard_Context_Model_for_Efficient_Learned_Image_Compression_CVPR_2021_paper.html) | 以 RD 损失很小为前提，重点报告 decoding speed 提升 | 对 tiled/precinct decoder，速度和并行/逐 tile 内存是正式审查项 |
| 65 | He et al., [ELIC](https://arxiv.org/abs/2203.10886) | compression ability 和 running speed 一起报告 | #824 PR 可以把 `ATR00049`、`DSC00157_1` 的 wall time 作为固定 baseline |
| 66 | Qian et al., [Contextformer](https://arxiv.org/abs/2203.02452) | PSNR/MS-SSIM/rate savings，同时讨论复杂 context modeling | Sony 的 green predictor clamp 与 Nikon 的 cross-tile carry 都应写成“context state correctness”问题 |
| 67 | Koyuncu et al., [Efficient Contextformer](https://arxiv.org/abs/2306.14287) | Kodak/CLIC2020/Tecnick，VTM anchor，rate savings，complexity，speed | 评价私有 RAW decoder 时也要指定 anchor：Adobe DNG Converter、Sony Imaging Edge、Nikon NX Studio 或 LibRaw private |
| 68 | Mentzer et al., [High-Fidelity Generative Image Compression](https://arxiv.org/abs/2006.09965) | FID/KID/LPIPS/NIQE、人类 study、rate，承认 PSNR tradeoff | 感知型指标越强，越要警惕它和 pixel fidelity 方向相反；RAW 解码不追求“生成得像” |
| 69 | Theis et al./Santurkar 类生成压缩讨论与 [Diff-ICMH](https://arxiv.org/abs/2511.22549) | 机器视觉、人类视觉和生成先验同时进入评价 | 若未来研究“RAW 压缩是否影响后续 AI/ISP”，可加下游任务；当前 #824/#826 不应扩展成这个问题 |
| 70 | Google, [WebP Compression Study](https://developers.google.com/speed/webp/docs/webp_study) | Same-SSIM file size、SSIM-bpp 曲线、多数据集和可复现命令 | PR 里的压缩率结论必须说明样本来源和内容；`DSC00157_1` 的高压缩比不能代表平均水平 |
| 71 | Bellard, [BPG Image format](https://bellard.org/bpg/) | 用 HEVC intra 作为强 still-image baseline，比较 quality/size | baseline 选择影响结论；#824/#826 的 baseline 应是 reference decoder，不是另一个视觉 codec |
| 72 | JPEG 2000, [official documentation](https://jpeg.org/jpeg2000/) | conformance、reference software、high bit-depth、tiles/precincts/code-blocks | Nikon HE 的 precinct/tile 与 Sony LLVC3 tile/guard 行可以借鉴 JPEG 2000 的边界审查思路 |
| 73 | JPEG-LS, [official documentation](https://jpeg.org/jpegls/) | lossless/near-lossless、低复杂度、误差界 | RAW decoder 报告里 `max_abs` 和误差直方图是强证据，比平均 PSNR 更直观 |
| 74 | JPEG XL, [official documentation](https://jpeg.org/jpegxl/documentation.html) and [libjxl](https://github.com/libjxl/libjxl) | 标准语法、reference implementation、lossless/lossy/HDR/metadata | 私有 RAW 逆向没有标准时，必须用 reference-output compare 与公开命令弥补 |
| 75 | JPEG AI, [Common Test Conditions](https://jpeg.org/items/20201028_jpeg_ai_common_test_conditions.html) | 明确 datasets、anchors、target rates、objective/subjective procedures | #824/#826 应把“样本矩阵 + reference version + commands + JSON/CSV”当作审查合同 |
| 76 | Richter et al., [Bayer CFA Pattern Compression With JPEG XS](https://cris.fau.de/publications/262429657/) | Bayer CFA 直接压缩，比较 RGB workflow 的 bitrate、quality、complexity，报告 1.5 dB 到 4 dB 级收益 | RAW 压缩应优先在 CFA mosaic 域评价；demosaic 后图像会引入 ISP/插值变量 |
| 77 | JPEG XS, [RAW image compression in-depth](https://ds.jpeg.org/documents/jpegxs/wg1n100275-096-COM-JPEG_XS_in-depth_series_raw_image_compression.pdf) | RAW profile、bit depth、Star-Tetrix、lossless/visually-lossless factor | Nikon HE/TicoRAW 可以类比 JPEG XS RAW 生态；Sony LLVC3 只能说结构相似，不能宣称标准等同 |
| 78 | intoPIX, [TicoRAW for Nikon Z 9](https://www.intopix.com/blogs/post/TicoRAW-technology-added-with-High-Efficiency-RAW-recording-of-Nikon-Z-9-flagship-mirrorless-camera) | 厂商材料强调 full sensor quality、低功耗、带宽/存储降低 | 可作为 Nikon HE/TicoRAW 背景证据，但不是 #826 每个 private quirk 的 conformance 证据 |
| 79 | Chung et al., [Compression for Bayer CFA Images: Review and Performance Comparison](https://www.mdpi.com/1424-8220/22/21/8362) | 总结 CF/DF/RCT 等 Bayer 工作流，常用 PSNR/SSIM/FSIM、bitrate、真实 CFA 场景 | 支持本项目按 R/G0/G1/B 分相位、按 active area 比较，而不是只看 RGB preview |
| 80 | Bazhyna and Egiazarian, [Lossless and Near Lossless Compression of Real CFA Data](https://researchportal.tuni.fi/en/publications/lossless-and-near-lossless-compression-of-real-color-filter-array) | 强调 real camera CFA data、lossless/near-lossless 压缩效率 | #824/#826 必须使用真实 ARW/NEF；RGB 合成伪 CFA 只能做 toy test |
| 81 | Somasundaram and Domnic, [Visually lossless Bayer CFA VQ](https://www.sciencedirect.com/science/article/abs/pii/S1568494615008042) | RAW demosaic 前压缩，比较 PSNR 和 visually lossless 目标 | 若讨论 Nikon/Sony 厂商“视觉无损”，需单独证明；LibRaw PR 则先验证解码输出 |
| 82 | ITU-R, [BT.500](https://www.itu.int/rec/R-REC-BT.500) | 主观图像质量的观看条件、DSIS/DSCQS、统计处理 | “视觉无损”不能靠随意肉眼判断；需要正式 observer protocol |
| 83 | ITU-T, [P.910](https://www.itu.int/ITU-T/recommendations/rec.aspx?rec=9317) | ACR/DCR/CCR/SAMVIQ 等 multimedia subjective methods | 对 codec 产品评价有用；对 decoder PR 只是辅助层 |
| 84 | ISO, [ISO/IEC 29170-2:2015](https://www.iso.org/standard/66094.html) | nearly lossless coding 的主观评价方法 | 适合评价 HE/CRAW HQ 是否 visually lossless，不适合替代 reference exactness |
| 85 | Herglotz et al., [The Bjontegaard Bible](https://arxiv.org/abs/2304.12852) | 讨论 BD-rate/BD-PSNR 插值、饱和指标和误用 | 没有多码率点时不要乱算 BD-rate；#824/#826 当前更适合 sample-wise bpp + diff |
| 86 | LibRaw #824, [Sony ARW6 CRAW HQ PR](https://github.com/LibRaw/LibRaw/pull/824) | 实际审查已经使用 Adobe DNG Converter、共同 crop、highlight diff、R/B LUT step 归因 | #824 是把论文式 conformance 方法落到私有 RAW decoder 的好模板 |
| 87 | LibRaw #826, [Nikon HE PR](https://github.com/LibRaw/LibRaw/pull/826) | PR 描述 HE/HE* 分流、JPEG-XS-like DWT、tone LUT、end-to-end NEF 测试 | #826 需要补与 #824 同等的 reference compare、样本矩阵、HE* unsupported 行为和安全审查 |

## 读完后的评价模板

### Encoder 论文模板

| 模块 | 必填证据 |
|---|---|
| Dataset | Kodak/Tecnick/CLIC/DIV2K/真实 RAW；训练集与测试集分开 |
| Rate | bpp、compressed bytes、target rate、rate control 是否稳定 |
| Distortion | MSE、PSNR、MAE、RMSE、max error、per-image outliers |
| Perception | SSIM/MS-SSIM/FSIM/VIF/LPIPS/DISTS/Butteraugli/VMAF，说明指标适用域 |
| RD summary | 多 bitrate RD 曲线，必要时 BD-rate/BD-PSNR，并说明插值方法 |
| Subjective | MOS、pairwise preference、BT.500/P.910/CLIC-style human study |
| Complexity | encode/decode time、latency、CPU/GPU/FPGA、MACs、memory、decoder size |
| Robustness | malformed stream、tile independence、buffer/padding、random access |
| Reproducibility | code、reference implementation、版本、命令、输出表 |

### RAW decoder PR 模板

| 模块 | #824/#826 应写的证据 |
|---|---|
| Container dispatch | Compression tag、photometric、bits/sample、strip offset/byte count、raw size |
| Bitstream structure | stream magic、tile/precinct count、packet lengths、padding、guard rows、tail behavior |
| Reference output | Adobe DNG Converter / Sony Imaging Edge / Nikon NX Studio / LibRaw private reference |
| Alignment | common active area、black level、white level、CFA phase、crop origin |
| Pixel diff | exact/nonzero/max_abs/mean_abs/RMSE/histogram、R/G0/G1/B、highlight subset |
| Error attribution | predictor、LUT/tone curve、rounding/clamp、tile edge、cross-tile carry |
| Variant matrix | A000 vs 0000 Sony streams；Nikon Z9/Z8/Zf/Z6III；FF/DX；HE/HE* |
| Safety | allocation/overflow/bounds/cancel/corrupt stream behavior |
| Performance | R6/M5 wall time、memory estimate、one-tile-at-a-time decode |
| Remaining gaps | unsupported variants, unresolved review comments, missing public samples |

## 直接映射到当前工作

- #824 Sony CRAW HQ：优先继续把 `compare_raw_outputs.py` 用在 LibRaw TIFF vs Python guarded reference vs Adobe/Sony reference 上，正式保存 JSON/CSV。重点子集是 R/B highlights、tile guard rows、black-level convention、`dng_levels` review cleanup。
- #826 Nikon HE：优先补样本和参考输出，不要只相信 PR 自述。最小矩阵应覆盖 Z8/Zf 已找到的 `Bp=4` HE 成功路径、`Bp=2/3` HE* unsupported 路径、负样本，以及缺失的 Z6III/DX crop。
- 两者共同：PR 评论应写成“样本、reference decoder/version、共同 crop、逐像素统计、差异归因、runtime/memory、unsupported variants”，这比“看起来正常”更接近编码器论文/标准的证据语言。
