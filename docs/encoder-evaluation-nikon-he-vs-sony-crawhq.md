# 图像编码器评价方法笔记：Nikon HE 与 Sony ARW6 CRAW HQ 的 LibRaw 审查语境

日期：2026-06-01  
工作对象：LibRaw/LibRaw#826 Nikon HE、LibRaw/LibRaw#824 Sony ARW6 CRAW HQ、当前本地 `Jiangtherapee-sony-craw-hq-decoder` 与 `LibRaw-pr-sony-arw6-craw-hq`

## 结论先行

图像编码器论文很少只说“能解码”或“视觉看起来正常”。主流评价通常分成四层：

1. **正确性/一致性**：标准比特流、参考解码器、厂商/Adobe/Nikon/Sony 输出、位精确或误差直方图。
2. **压缩效率**：bits-per-pixel、压缩比、RD 曲线、BD-rate/BD-PSNR，多码率点而不是单点。
3. **质量**：PSNR/MSE、SSIM/MS-SSIM、FSIM/VIF、LPIPS/DISTS/Butteraugli/VMAF，以及必要时主观测试。
4. **工程性**：编码/解码速度、内存峰值、延迟、硬件可实现性、tile/edge/metadata/错误流安全性。

对 #824/#826 这种“已有相机编码器、我们做开源解码器”的 PR，最强证据不是 RD 曲线，而是：**对相机真实样本的结构解析正确、与可信参考输出在共同有效区域逐像素一致或差异可解释、边界/高光/裁剪/黑白电平/多机型变体覆盖充分**。

## LibRaw PR 事实核对

### #826 Nikon HE

来源：<https://github.com/LibRaw/LibRaw/pull/826>

- PR 标题：`Add Nikon Z9 High-Efficiency (HE) RAW format support`。
- 状态：截至 2026-06-01 仍为 open。
- PR API 数据：创建于 2026-05-22，3 commits，约 4307 additions / 83 deletions / 42 files。
- PR 描述称 Nikon HE 是 “JPEG-XS-like 2D 5/3 wavelet codec”，用于 Z 9、Z 8、Z f、Z 6 III。
- 解码结构包括 precinct header parse、GCLI/coefficient/sign entropy decode、dequantization、horizontal + vertical inverse 5/3 DWT、tile orchestration、step1/step2 Bayer reconstruction、piecewise-linear tone-curve LUT。
- 代码层面，`nikon_he_load_raw()` 从 strip offset + `0x9b` 读 precinct stream，HE 使用 first `Bp` 为 4/5，HE* 的 `Bp` 为 1/2/3 当前显式拒绝，成功后把 14-bit RGGB 写入 `raw_image` 并设置 `maximum = 16383`。
- 讨论中有三条关键证据：
  - LibRaw 维护者说 #826 需要测试，同时他们正在测试 #824：<https://github.com/LibRaw/LibRaw/pull/826#issuecomment-4524284012>
  - Alexey-Danilchenko 提到 LibRaw 私有分支已有 Nikon HE/HE* TicoRAW 解码器，并用于 FRV/RawDigger：<https://github.com/LibRaw/LibRaw/pull/826#issuecomment-4524662360>
  - LibRaw 维护者说 HE/HE* decoder 很可能在 2026 年秋季的 public snapshot 中包含：<https://github.com/LibRaw/LibRaw/pull/826#issuecomment-4525790231>

本地临时 clone 的 #826 分支为 `8aebd05`。代码证据点：

- `src/decoders/nikon_he_decoder.cpp`：LibRaw glue、strip 读取、HE/HE* 分流、`maximum = 16383`。
- `src/decoders/nikon_he/nikon_he_decode.cpp`：image-level pipeline，按 tile/precinct 组织，三 pass：decode tiles、step1、step2。
- `src/decoders/nikon_he/nikon_he_bayer.cpp`：最终 Bayer reconstruction，含 tone-curve LUT lookup。
- `src/decoders/nikon_he/nikon_he_tile.h`：每 tile 18 precinct、32 stripes、2 rows overflow carry。

### #824 Sony ARW6 CRAW HQ

来源：<https://github.com/LibRaw/LibRaw/pull/824>

- PR 标题：`Add Jiangtherapee Sony ARW6 CRAW HQ decoder`。
- 状态：截至 2026-06-01 仍为 open。
- PR API 数据：创建于 2026-05-20，4 commits，约 1685 additions / 11 deletions / 14 files。
- PR 描述称新增 `sony_arw6_load_raw()`，把 Sony TIFF `Compression=32766` ARW6 raw IFD dispatch 到新解码器，接入 Makefile/qmake/VS，并在 `get_decoder_info()` 暴露。
- 本地 PR 仓库 `C:\Users\姜尧耕\Downloads\LibRaw-pr-sony-arw6-craw-hq` 当前 HEAD 为 `1fa7855c Match Adobe highlight handling in Sony ARW6 decoder`。
- 本地代码证据：
  - `src/decoders/sony_arw6.cpp`：单文件解码器；解析 stream header/directory/packet，处理 single-stream 与 tiled streams。
  - `src/metadata/tiff.cpp`：遇到 Sony、非 DNG、photometric CFA、samples=1、12/14 bps、Compression=32766 时 dispatch 到 `sony_arw6_load_raw()`，并把 `tiff_bps` 设为 14、black 设为 1024。
  - `tools/llvc3_math.py`：最终颜色关系已经记录关键发现：Sony 在 R/B predictor 使用前把 final green pair clamp 到 12-bit code domain，否则高光会泄漏 1-3 个 LUT code step。
- #824 讨论中的关键测试事实：
  - A7R6 是 2x2 tiled LLVC3，tile 高度 3336 不是 16 对齐，需要 padded height 与特殊 guard/flush row 映射：<https://github.com/LibRaw/LibRaw/pull/824#issuecomment-4526554394>
  - LibRaw 用 Adobe DNG Converter 18.3.1 作参考实现假设，对共同区域比较，绝大多数像素一致，高光 R/B 从约 14000 开始有 1-3 个 LUT step 差异：<https://github.com/LibRaw/LibRaw/pull/824#issuecomment-4540912645>
  - 后续修复说明根因是 final_green 高光 overshoot 参与 R/B predictor，clamp 后匹配 Adobe/Sony native output：<https://github.com/LibRaw/LibRaw/pull/824#issuecomment-4544508558>
  - 维护者指出 `tiff_ifd[raw].dng_levels` 在非 DNG Sony 分支不必赋值，属于可读性问题：<https://github.com/LibRaw/LibRaw/pull/824#discussion_r3304040579>

本地 Python 解码输出证据：

- `out/libraw_perf_guard_ATR00049/ATR00049_llvc3_pure_summary.json`
  - 输入 `ATR00049.ARW`，raw `10016x6672`，Compression `32766`，WhiteLevel `16383`。
  - 4 个 LLVC3 streams，2x2 tiles，每 tile `5008x3336`，magic `0000`，versions `0x01000000` 到 `0x04000000`。
  - `tile_edge_mitigation.enabled=false`，`native_edge_oracle.enabled=false`，说明当前 guarded path 不靠边缘修补或 native oracle。
- `out/libraw_perf_guard_DSC00157/DSC00157_1_llvc3_pure_summary.json`
  - 输入 `DSC00157_1.ARW`，raw `7040x4688`，single stream，magic `A000`。
  - 同样输出 RGGB raw、preview、DNG-like TIFF 与 summary。

### Nikon HE 与 Sony CRAW HQ 的技术相似点

- 都是 Bayer/CFA RAW 语境，不应只用 sRGB 预览评价。
- 都是 wavelet-like 高效率 RAW 压缩：Nikon #826 明确接近 JPEG XS/TicoRAW，Sony LLVC3 本地代码也有分层 5/3-like inverse 与 residual/predictor 结构。
- 都有非线性/分段 LUT 或 tone curve：Nikon 有 `iqx/iqp` tone curve LUT，Sony 有 12-bit internal code 到 sample 的 4096-entry LUT。
- 都有 tile/precinct/edge carry 类问题，边界处理不是“小尾巴”，而是能造成彩边、高光偏差或 tile artifact 的核心正确性问题。
- 都需要通过共同裁剪区域比较：Adobe/Sony/Nikon 工具可能裁剪 DNG 或处理黑电平，比较前必须对齐 active area、black level、white level、CFA phase。

### Nikon HE 与 Sony CRAW HQ 的差异

- Nikon HE 更接近公开标准生态：JPEG XS/TicoRAW、precinct、GCLI、dequant、DWT、Star-Tetrix 讨论都有公开文献或社区证据。但 #826 不是 LibRaw 私有 HE/HE* decoder，且 PR 只支持 HE，明确拒绝 HE*。
- Sony ARW6 CRAW HQ 更像厂商私有 LLVC3 逆向：没有找到能证明 ARW6/LLVC3 完整格式的公开标准。#824 的强证据主要来自 Imaging Edge/native dump、Adobe DNG Converter 对比、真实样本、局部误差归因。
- Nikon #826 代码量大而模块多，审查重点包括内存/越界/错误流与 HE* 拒绝路径；Sony #824 代码较集中，审查重点是 tiled/guarded-height、LUT clamp、高光与 metadata integration。

## 从论文中抽出的评价框架

### 1. Rate-distortion 是编码器论文的主轴

典型做法：

- 对同一测试集，在多个 bitrate 或 quality 参数下编码。
- 记录 bit-per-pixel 或压缩比。
- 对每个点计算 PSNR、MS-SSIM 等 distortion/quality。
- 画 RD 曲线，必要时计算 BD-rate 或 BD-PSNR。
- 不只报告平均值，还看每张图或每类图的离群点。

对应到 RAW 解码器：如果我们没有实现 encoder，RD 曲线不是 #824/#826 的核心 gate；但“bitstream size vs raw size”的压缩比、样本覆盖、decoded output vs reference 的误差分布仍然必须记录。

### 2. PSNR/MSE 仍常用，但不能单独代表可见质量

PSNR/MSE 适合检查数值误差，特别适合 decoder conformance、黑白电平、高光溢出、LUT step 差异。但它会把所有像素误差当作独立且等权，不能很好解释结构和感知质量。

RAW 场景下 PSNR 的一个陷阱：如果在 demosaic 后 sRGB 上算，误差可能被色彩管理、降噪、gamma、裁剪掩盖；如果在 RAW mosaic 上算，又要按通道/黑电平/active area 分开看。

### 3. SSIM/MS-SSIM/FSIM/VIF/LPIPS/DISTS 是感知补充

这些指标常用于图像压缩论文，但对 #824/#826 只能作为补充，因为我们审查的是“解码是否忠实”，不是“压缩后是否好看”。如果 decoder 与 Adobe 差 1-3 个 LUT step，高 PSNR 或高 MS-SSIM 不能替代逐像素差异归因。

### 4. 主观测试用于“visually lossless”声明

JPEG XS、AIC-2/ISO 29170-2、ITU-R BT.500、ITU-T P.910 都强调主观方法的严谨性。若 Nikon/Sony 厂商声称 HE/CRAW HQ visually lossless，论文级验证需要 observer test；但 LibRaw decoder PR 的目标更窄：重建厂商/参考解码输出。

### 5. 工程指标在现代 codec 论文里越来越重要

JPEG XS 和 learned compression 论文都不只看质量：

- latency：行延迟、帧延迟、pipeline depth。
- complexity：CPU/GPU/FPGA/ASIC 资源、decode/encode speed、power。
- memory：tile buffer、working set、stream buffering。
- parallelism：tile、stripe、checkerboard/context model。
- robustness：错误流、padding、marker、tile bounds、cancel。

这直接映射到 LibRaw 审查：`data_size` 上限、tile 坐标检查、packet length 检查、`max_raw_memory_mb`、`checkCancel()`、避免一次性分配所有 tile 的工作面。

## 论文与资料清单：它们如何评价编码器

更细的逐条矩阵见：[`codec-paper-evaluation-matrix.md`](codec-paper-evaluation-matrix.md)。它把论文/标准分解为 conformance、rate、distortion、perception、subjective、complexity、robustness、reproducibility 等评价维度，并分别映射到 #824/#826 的审查项。

本轮扩展阅读见：[`codec-evaluation-literature-reading-notes.md`](codec-evaluation-literature-reading-notes.md)。它把 CLIC、CompressAI、RDP tradeoff、JPEG AI、Bayer CFA 压缩和主观评价标准整理成 #51-#87 的补充矩阵，核心结论是：RAW decoder PR 的主证据应是 reference conformance，而不是 encoder 论文里的单一感知指标。

系统性检索与证据缺口见：[`encoder-evaluation-source-audit-and-gap-map.md`](encoder-evaluation-source-audit-and-gap-map.md)。它记录了 2026-06-02 的新增检索、source quality tier、哪些结论证据强、哪些结论仍不能成立，尤其是 same-source multi-rate encoder 缺口。

控码率编码性能评价协议见：[`controlled-rate-encoder-benchmark-protocol.md`](controlled-rate-encoder-benchmark-protocol.md)。它把“假设同一个 Bayer 输入、控制码率、画 RD 曲线/算 BD-rate、看谁更好”的论文式实验拆成 L1 真实 encoder benchmark、L2 代理 encoder benchmark、L3 公开样本弱配对和 L4 decoder-only 数学对比四个证据等级。

样本与实证命令矩阵见：[`raw-decoder-evaluation-sample-matrix.md`](raw-decoder-evaluation-sample-matrix.md)。它记录了当前本地 Sony ARW6 样本的 bpp/压缩比/stream 布局，并列出 Nikon HE/HE* 还缺的样本类别。

面向 PR 审查的并排 playbook 见：[`libraw-824-826-evaluation-playbook.md`](libraw-824-826-evaluation-playbook.md)。它把文献评价维度、代码路径、样本 rate/structure、open review item 和 PR 评论格式放到同一张操作表里。

仅编码数学层面的对照见：[`encoder-math-only-nikon-he-vs-sony-crawhq.md`](encoder-math-only-nikon-he-vs-sony-crawhq.md)。如果问题是“两个压缩家族从数学上谁更像可率控 encoder、谁更像固定相机管线”，应优先读那份，而不是把 decoder 安全审查当作 encoder 优劣结论。

| # | 来源 | 关注点 | 评价方法/对本任务启发 |
|---|---|---|---|
| 1 | Descampe et al., “JPEG XS - A New Standard for Visually Lossless Low-Latency Lightweight Image Coding”, Proceedings of the IEEE, 2021 | JPEG XS 标准总体 | visually lossless、低延迟、低复杂度、标准 conformance 与主客观评价并重 |
| 2 | Richter et al., “Bayer CFA Pattern Compression With JPEG XS”, IEEE TIP 2021 | RAW Bayer/CFA 压缩 | 直接压 Bayer，比较 RGB workflow，报告质量增益、复杂度与 bitrate |
| 3 | JPEG XS raw image compression in-depth series, 2022 | RAW profiles | LightBayer/MainBayer/HighBayer，10/12/14/16-bit、Star-Tetrix、lossless/visually-lossless compression factor |
| 4 | JPEG XS objective evaluation procedures, SPIE 2017 | 标准提案评价 | 标准化测试序列、objective procedures、候选技术横向比较 |
| 5 | JPEG XS subjective evaluations, SPIE 2017 | 主观评价 | call-for-proposals 用主观测试筛选 visually-lossless 技术 |
| 6 | Willème et al., low-latency lightweight intra-frame codecs, JETCAS/DCC 2016 | 低延迟 intra codec | 质量和 error robustness 一起测，适合思考 RAW 文件损坏/边界问题 |
| 7 | JPEG XS FPGA entropy implementation, 2024 | 工程复杂度 | 不只看质量，还看 FPGA throughput/latency/resource |
| 8 | intoPIX TicoRAW Nikon Z9 press release | Nikon HE/TicoRAW 背景 | 公开确认 Nikon Z9 使用 TicoRAW 技术，强调 full sensor quality、低功耗、带宽/存储下降 |
| 9 | ISO/IEC 29170-2:2015 | nearly/visually lossless 主观测试 | 是否能被观察者区分，适合厂商级 visually lossless 声明 |
| 10 | ITU-R BT.500 | 主观图像质量 | DSIS/DSCQS、观看环境和统计处理，避免随意“看起来不错” |
| 11 | ITU-T P.910 | 多媒体主观质量 | ACR、DCR、CCR、SAMVIQ，适合 codec human study |
| 12 | JPEG AI Common Test Conditions | 学习式 codec 标准测试 | 定义 datasets、anchors、target bitrates、objective/subjective procedures |
| 13 | JPEG AI overview / variable-rate JPEG AI | 新一代学习式编码 | 多 perceptual metrics 的 BD-rate，不只 PSNR |
| 14 | JPEG XL white paper / ISO 18181 / libjxl | 标准和参考软件 | 标准 conformance、reference implementation、lossless/lossy/HDR/metadata |
| 15 | JPEG XL history/features paper, 2025 | JXL 设计 rationale | 编码工具、设计取舍、兼容性、参考实现和生态 |
| 16 | Committee Draft of JPEG XL, 2019 | 标准草案 | 用标准语法和参考软件定义可互操作解码 |
| 17 | JPEG XL subjective/objective assessment reports | JXL proposal evaluation | subjective methodologies + objective metrics，而不是单一 PSNR |
| 18 | HEVC still image/BPG comparisons | 视频 codec 做 still image | PSNR 与 MOS 都用；BPG/HEVC 常作为传统强 baseline |
| 19 | WebP objective assessment | WebP vs JPEG/JPEG2000/JPEG XR | PSNR/SSIM，多测试图像，客观指标横向比较 |
| 20 | Rippel & Bourdev, Real-Time Adaptive Image Compression, ICML 2017 | learned codec | 用 MS-SSIM target、文件大小、实时性和视觉对比 |
| 21 | Ballé et al., End-to-end optimized image compression, ICLR 2017 | learned transform coding | 以 rate-distortion objective 训练，比较 JPEG/JPEG2000，报告 MS-SSIM |
| 22 | Ballé et al., Scale hyperprior, ICLR 2018 | entropy model | PSNR 和 MS-SSIM 双指标；定性对比不同 distortion metric 训练 |
| 23 | Minnen et al., Joint autoregressive and hierarchical priors, NeurIPS 2018 | entropy/context model | 报告 rate reduction vs JPEG/JPEG2000/WebP/BPG，PSNR/MS-SSIM |
| 24 | Johnston et al., improved lossy image compression, CVPR 2018 | recurrent/adaptive rates | spatially adaptive bitrates，实际码率控制 |
| 25 | Cheng et al., GMM likelihoods + attention, CVPR 2020 | learned entropy model | Kodak/Tecnick/CLIC，PSNR/MS-SSIM，传统 codec baseline |
| 26 | Checkerboard Context Model, CVPR 2021 | decoder parallelism | RD 几乎不降，同时 decoding speed 大幅提升；工程性是指标 |
| 27 | ELIC, CVPR 2022 | 高效 learned codec | rate-speed comparison，Kodak/Tecnick/CLIC，compression ability + running speed |
| 28 | Contextformer, ECCV 2022 | transformer context model | Kodak/CLIC/Tecnick，对比 VTM 16.2，PSNR/MS-SSIM/rate savings |
| 29 | Koyuncu et al., [eContextformer](https://arxiv.org/abs/2306.14287), 2023 | 快速 context modeling | Kodak/CLIC2020/Tecnick、VTM anchor、rate savings、complexity、decoding speed |
| 30 | LIC-TCM, CVPR 2023 | Transformer-CNN mixture | BD-rate over VTM、Kodak/Tecnick/CLIC，参数量/复杂度 |
| 31 | CompressAI | reproducible learned compression | 统一库、模型、metric、dataset，强调可复现实验 |
| 32 | MSU / learning-based image compression benchmark | 真实 benchmark | 多模型统一输入、统一指标、可复现脚本 |
| 33 | Wang et al., SSIM, IEEE TIP 2004 | 结构相似 | JPEG/JPEG2000 subjective database 上验证；说明 PSNR 不够 |
| 34 | Wang et al., MS-SSIM, 2003 | 多尺度结构 | 多观看尺度下更接近感知 |
| 35 | Sheikh & Bovik, VIF, 2006 | 信息保真 | 以自然场景统计和视觉信息为基础 |
| 36 | Zhang et al., FSIM, 2011 | feature similarity | phase congruency/gradient magnitude，常见 full-reference metric |
| 37 | Zhang et al., LPIPS, CVPR 2018 | 深度感知指标 | 人类 pairwise judgment 数据，指出 PSNR/SSIM 对神经图像伪影不足 |
| 38 | Ding et al., DISTS, CVPR 2020 | structure/texture similarity | 感知结构与纹理分离，可补充 learned codec artifacts |
| 39 | PieAPP, 2018 | pairwise preference IQA | 从人类偏好学习 error metric |
| 40 | Bjøntegaard, VCEG-M33, 2001 | BD-rate/BD-PSNR | 用 RD 曲线平均差比较 codec，至少多个点 |
| 41 | Herglotz et al., Bjøntegaard Bible, 2023 | BD-rate 误差 | BD 计算有插值误差；VMAF/SSIM 等饱和指标要谨慎 |
| 42 | Compression for Bayer CFA Images review, Sensors 2022 | Bayer 压缩综述 | 比较 CF/DF/RCT 等 Bayer 工作流，使用 PSNR/SSIM/FSIM 和 bitrate |
| 43 | Visually lossless compression for Bayer CFA using optimized VQ, 2016 | Bayer visually lossless | PSNR 与视觉无损目标，强调 demosaic 前 RAW 压缩 |
| 44 | Review of IQA methods for compressed images, J. Imaging 2024 | 压缩图像指标综述 | JPEG/JPEG2000/JPEG XL/JPEG AI 的 artifact 和 metric 适用性 |
| 45 | Medical/OCT/underwater learned compression papers | 任务型图像压缩 | 除 PSNR/MS-SSIM 外，加入下游任务或领域图像特性 |

## 面向 #824/#826 的评价清单

### A. 文件结构与 dispatch

- RAW IFD 的 Compression、PhotometricInterpretation、BitsPerSample、SamplesPerPixel、strip offset/byte count 是否完全符合预期。
- Sony #824：Compression=32766 不等于旧 Sony ARW 32767；ExifTool 2026 页面把 32766 标为 “NeXt or Sony ARW Compressed 2”，旧表只写 NeXT，不能只靠旧 TIFF 表推断。
- Nikon #826：JPEG XS SOC marker 会同时路由 HE/HE*，必须显式证明 HE* 拒绝路径不会误输出坏图。
- 变体要覆盖：Sony `A000` single stream、`0000` tiled stream、sequence/version `0x01000000`/`0x02000000`；Nikon Z9/Z8/Zf/Z6III、FF/DX crop、HE/HE*。

### B. 参考输出一致性

最低要求：

- 与 Adobe DNG Converter / Sony Imaging Edge / Nikon NX Studio / 已知 LibRaw 私有参考输出比较。
- 只比较共同 active area，显式处理 default crop origin/size、black level、white level、CFA phase。
- 输出 per-channel 统计：exact count、nonzero count、max_abs、mean_abs、RMSE、first mismatch、diff histogram。
- 对高光区单独统计，比如 Sony 约 `sample >= 14000` 区间，因为 #824 的 R/B LUT step 差异就出现在这里。

建议报告格式：

| sample | camera | mode | reference | common crop | nonzero | max_abs | channels affected | explanation |
|---|---|---|---|---|---:|---:|---|---|

### C. RAW 特有的质量检查

- 不只看 preview PNG。RAW mosaic 上每个 CFA plane 都要统计。
- 分开看 code domain 与 sample/LUT domain。Sony 的 12-bit internal code 到 14/16-bit sample LUT 会把 1 code step 变成不同 sample step。
- 对红蓝残差和绿通道 predictor 单独做局部检查。Sony #824 的关键 bug 正是 final green predictor clamp 顺序。
- 检查黑电平：Sony 本地 PR 设置 LibRaw black=1024，但样本 metadata 的 tag 可能记录 512；必须解释这是 LibRaw 14-bit 输出约定还是 DNG-like shifted output。
- 检查白电平和 saturated/highlight 行为：高光会放大 rounding/clamp 细节。

### D. 边界、tile 与错误流

Sony #824：

- R6 tiled `10016x6672`，4 streams，tile `5008x3336`，3336 不是 16 对齐。
- guarded path 必须保持：
  - `padded_height = align_up(coded_height, 16)`
  - `low_start = 1` when guarded
  - group1/2/3 guard synthesis
  - final green top rows `2` for guarded R6, otherwise `4`
  - `tile_edge_mitigation.enabled=false`
  - `native_edge_oracle.enabled=false`

Nikon #826：

- 每 tile 18 precinct、2-precinct overlap、alignment pad、cross-tile carry。
- 检查每个 precinct size、padding、sentinel、tile tail、partial crop。
- 目前 PR 把失败 decode memset 为 0 并 return，而不是 throw；这需要审查是否符合 LibRaw 错误语义。

通用安全：

- 对 `data_size`、`stream_size`、packet length、tile coordinates、rows*cols、working memory 使用 64-bit 中间值。
- 不让 corrupt file 触发越界读/写、整数溢出、超限内存。
- 关键循环里保留 `checkCancel()`。

### E. 速度和内存

编码器论文常报告 speed/latency；LibRaw decoder PR 也应该报告：

- decode wall time：至少 target sample + regression sample。
- memory estimate：raw bytes + compressed strip + per-tile working bytes。
- 是否按 tile decode/copy，避免一次性展开所有 tile。
- 单线程 baseline 与未来优化空间。

当前 Sony skill baseline：

- R6 `ATR00049.ARW`：约 2.3 s，输出 `10016x6672`。
- M5 `DSC00157_1.ARW`：约 1.2 s，输出 `7040x4688`。
- `raw-identify`：Sony `ILCE-7RM6`，black `1024`，RGGB，raw colors `3`。

## 建议下一步实证工作

1. **为 #824 固化一个 compare harness**  
   输入 LibRaw TIFF、Python guarded reference raw、Adobe DNG common crop，输出 JSON/CSV：per-channel max_abs、nonzero、histogram、highlights subset。

2. **把 #826 的 Nikon HE PR 做同等审查表**  
   至少找 Z9 FF HE、DX crop HE、Z8/Zf/Z6III HE 样本；HE* 样本用于确认拒绝路径。若拿不到 Adobe/Nikon reference raw，可先用 NX Studio/Adobe DNG 转换后的 active area 作为准参考。

3. **写 PR 评论时用论文式语言**  
   不写“看起来好了”，写：
   - dataset/sample list
   - reference decoder and version
   - crop alignment
   - exact pixel statistics
   - channel/highlight/tile-boundary diff analysis
   - runtime/memory
   - remaining unsupported variants

4. **不要把标准证据过度外推**  
   JPEG XS Bayer/TicoRAW 资料能支持 Nikon HE 的“大类结构”理解；不能证明 #826 每个 private quirk。Sony 公开资料更少，#824 结论要明确写成“由样本、native/Adobe 对比和逆向代码证明”，不要写成“Sony 标准如此规定”。

## 已落地的比较工具

新增工具：`tools/compare_raw_outputs.py`

用途：比较两个 uint16 CFA-domain 输出，可以是 `.raw/.bin` 或 TIFF/DNG-like 文件。它把图像编码器论文里的“误差统计/高光子集/按通道报告”落成 decoder conformance 报告，适合 #824 Sony CRAW HQ，也能用于 #826 Nikon HE 的 NX Studio/Adobe DNG/reference 输出对照。

核心输出：

- `overall`：总样本数、是否 exact、nonzero、nonzero_pct、max_abs、mean_abs、RMSE、signed_mean、abs diff percentiles、abs diff histogram。
- `by_site`：RGGB 的 `R/G0/G1/B/G` 分相位统计，避免绿色相位反了还被整体平均掩盖。
- `highlight`：默认 `>=14000` 的高光子集统计，专门对应 #824 里 Adobe 发现的 R/B high highlight LUT-step 差异。
- `lut_code_domain`：可传入 Sony `tools/data/sony_llvc3_static_lut4096_padded_u16.bin`，把 sample 反查到最近 code，报告 code-domain diff。
- `first_mismatch_xy`：第一处像素差异的坐标和值，便于回到 tile/packet/edge trace。

示例：

```powershell
python tools\compare_raw_outputs.py `
  --candidate out\libraw_perf_guard_ATR00049\ATR00049_llvc3_pure_rggb_10016x6672_u16.raw `
  --reference out\libraw_perf_guard_ATR00049\ATR00049_llvc3_pure_rggb_10016x6672_u16.raw `
  --shape 10016x6672 `
  --label ATR00049_self `
  --out-json out\compare_reports\ATR00049_self.json `
  --out-csv out\compare_reports\ATR00049_self.csv
```

用于 Sony LUT/code-domain：

```powershell
python tools\compare_raw_outputs.py `
  --candidate candidate_from_libraw.tiff `
  --reference reference_from_adobe_or_python.raw `
  --reference-shape 10016x6672 `
  --candidate-crop 0,0,9984,6656 `
  --reference-crop 12,8,9984,6656 `
  --lut tools\data\sony_llvc3_static_lut4096_padded_u16.bin `
  --label ATR00049_common_crop `
  --out-json out\compare_reports\ATR00049_common_crop.json `
  --out-csv out\compare_reports\ATR00049_common_crop.csv
```

本轮验证：

- `python -m py_compile tools\compare_raw_outputs.py` 通过。
- `ATR00049` raw 自比：`nonzero=0`，`max_abs=0`。
- `DSC00157_1` raw 自比 + Sony LUT inverse：`nonzero=0`，`max_abs=0`。
- `DSC00157_1` DNG-like TIFF vs raw：`nonzero=0`，`max_abs=0`。
- 4x4 合成 mismatch：脚本正确报告 3 个差异、`max_abs=4`，并把高光差异归到 `B` site。
- 2026-06-01 复跑 `ATR00049_self_latest` 与 `DSC00157_self_latest`：两个 raw 自比均为 `nonzero=0`、`max_abs=0`，输出位于 `out/compare_reports/`。

当前开放审查项：

- #824 的 review comment 指出 `src/metadata/tiff.cpp` 中非 DNG Sony 分支不必给 `tiff_ifd[raw].dng_levels` 赋值；本地 `LibRaw-pr-sony-arw6-craw-hq` 的 `1fa7855c` 仍保留这些赋值，后续 PR 清理应移除或给出明确理由。
- #826 仍缺与 #824 等价的公开 reference compare：至少要有 HE 成功路径、HE* unsupported 路径、负样本、共同 active-area diff、runtime/memory 与错误流行为。

## 参考链接

- LibRaw #826 Nikon HE PR：<https://github.com/LibRaw/LibRaw/pull/826>
- LibRaw #824 Sony ARW6 CRAW HQ PR：<https://github.com/LibRaw/LibRaw/pull/824>
- 扩展阅读笔记：[`codec-evaluation-literature-reading-notes.md`](codec-evaluation-literature-reading-notes.md)
- JPEG XS documentation and publications：<https://jpeg.org/jpegxs/documentation.html>
- JPEG XS raw image compression in-depth PDF：<https://ds.jpeg.org/documents/jpegxs/wg1n100275-096-COM-JPEG_XS_in-depth_series_raw_image_compression.pdf>
- Bayer CFA Pattern Compression With JPEG XS：<https://pubmed.ncbi.nlm.nih.gov/34270422/>
- intoPIX TicoRAW Nikon Z9 announcement：<https://www.intopix.com/blogs/post/TicoRAW-technology-added-with-High-Efficiency-RAW-recording-of-Nikon-Z-9-flagship-mirrorless-camera>
- ISO/IEC 29170-2 nearly lossless evaluation：<https://www.iso.org/standard/66094.html>
- ITU-T P.910：<https://www.itu.int/ITU-T/recommendations/rec.aspx?rec=9317>
- JPEG AI Common Test Conditions：<https://jpeg.org/items/20201028_jpeg_ai_common_test_conditions.html>
- JPEG XL documentation：<https://jpeg.org/jpegxl/documentation.html>
- ISO/IEC 18181-1 JPEG XL：<https://www.iso.org/standard/85066.html>
- libjxl reference implementation：<https://github.com/libjxl/libjxl>
- End-to-end optimized image compression：<https://arxiv.org/abs/1611.01704>
- Scale hyperprior：<https://arxiv.org/abs/1802.01436>
- Joint autoregressive and hierarchical priors：<https://papers.nips.cc/paper/8275-joint-autoregressive-and-hierarchical-priors-for-learned-image-compression>
- Cheng et al. GMM + attention：<https://arxiv.org/abs/2001.01568>
- Checkerboard Context Model：<https://openaccess.thecvf.com/content/CVPR2021/html/He_Checkerboard_Context_Model_for_Efficient_Learned_Image_Compression_CVPR_2021_paper.html>
- ELIC：<https://arxiv.org/abs/2203.10886>
- Contextformer：<https://arxiv.org/abs/2203.02452>
- LIC-TCM：<https://github.com/jmliu206/LIC_TCM>
- SSIM：<https://www.cns.nyu.edu/~lcv/pubs/makeAbs.php?loc=Wang03>
- MS-SSIM：<https://www.cns.nyu.edu/~lcv/pubs/makeAbs.php?loc=Wang03b>
- LPIPS：<https://arxiv.org/abs/1801.03924>
- VIF：<https://live.ece.utexas.edu/research/Quality/VIF.htm>
- Bjøntegaard original record：<https://cir.nii.ac.jp/crid/1570009749353497472>
- Bjøntegaard Bible：<https://arxiv.org/abs/2304.12852>
- Compression for Bayer CFA Images review：<https://www.mdpi.com/1424-8220/22/21/8362>
- ExifTool EXIF Compression values：<https://exiftool.org/TagNames/EXIF.html?mobile-app=true&theme=false>
