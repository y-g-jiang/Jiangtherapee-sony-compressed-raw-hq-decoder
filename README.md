# Jiangtherapee Sony cRAW HQ decoder

针对 Sony ARW6 / cRAW HQ 中 LLVC3 压缩流的逆向解码

**精度**

| 平面 | 当前结果 |
| --- | --- |
| c0 / green | 完全一致，0 个差异像素 |
| c1 / red | 仅 6 个像素差异，最大差异 1 个 LLVC3 码值 |
| c2 / blue | 仅 5 个像素差异，最大差异 1 个 LLVC3 码值 |

```text
sony-craw-hq-llvc3-decoder/
  README.md
  requirements.txt
  requirements-reverse.txt
  tools/
    llvc3_pure_decode.py          # 纯 Python 主解码器
    llvc3_entropy.py              # LLVC3 包和系数熵解码
    llvc3_math.py                 # 分层重建、小波逆变换、码值/LUT 处理
    llvc3_bitstream_probe.py      # ARW6 / LLVC3 结构探测
    recombine_llvc_planes.py      # 三平面合成 RGGB，并写预览/TIFF
    extract_llvc3_static_lut.py   # 从本机 Imaging Edge 提取静态 LUT
    data/
      sony_llvc3_static_lut4096_padded_u16.bin
      sony_llvc3_static_lut4096.tsv
```

**安装**

见requirements.txt

**使用**

从项目根目录运行

```powershell
python tools\llvc3_pure_decode.py C:\path\to\sample.ARW --out-dir out\decode_sample
```

| 输出 |  |
| --- | --- |
| `*_llvc3_pure_v0_c0.bin` | 绿色 |
| `*_llvc3_pure_v0_c1.bin` | 红色 |
| `*_llvc3_pure_v0_c2.bin` | 蓝色 |
| `*_llvc3_pure_rggb_*_u16.raw` | RGGBRW |
| `*_llvc3_pure_preview.png` | 预览图 |
| `*_llvc3_pure_*.tiff` | 带基础 DNG/TIFF 标签的线性 RAW 容器 |
| `*_llvc3_pure_summary.json` | 解码统计、包信息、输出路径和可选 native 比对结果 |

未经过 Sony LUT 展开的内部码域：

```powershell
python tools\llvc3_pure_decode.py C:\path\to\sample.ARW --out-dir out\code_domain --no-sample-lut
```

第一层是包和熵码，ARW6 内部的压缩流被分成多个 group/component packet，每个 packet 存放一组低频基底或高频差分系数。`llvc3_entropy.py` 负责把这些 bitstream 还原成整数系数。

然后分层空间重建，接近整数 5/3 小波逆变换。颜色平面重建，绿色通道作为主亮度骨架，红色和蓝色主要以相对绿色的残差形式存储。最终再把三个半分辨率颜色平面合回 RGGB Bayer 排列，并通过 Sony 静态 LUT 从内部码值展开到实际样本值。
