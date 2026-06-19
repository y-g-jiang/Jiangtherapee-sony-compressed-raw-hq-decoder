# bpp--质量点云遍历结果

## 运行目的

roundtrip/核心语法层已经闭合后，单条 canonical 曲线不足以回答“给定 bpp 到底有多少种质量”。因此新增 `tools/exhaustive_bpp_quality_cloud.py`，直接枚举 decoder-visible policy knobs，输出 `actual syntax bpp -> RAW sample-domain quality` 点云。

这个脚本不输出 `target_bpp` 列；性能横轴只使用 actual syntax bpp。

## 当前完成的全量点云

命令：

```powershell
python tools\exhaustive_bpp_quality_cloud.py `
  --out-dir out\exhaustive_bpp_quality_cloud_current_20260605 `
  --sony-policy-mode current `
  --sony-base-step-count 24 `
  --nikon-offset-mode current `
  --nikon-bias-count 7 `
  --jobs 16 `
  --max-scatter-points 180000
```

输出目录：

`out/exhaustive_bpp_quality_cloud_current_20260605`

manifest 摘要：

| 项 | 值 |
|---|---:|
| scenes | 24 |
| jobs requested | 16 |
| cpu count | 16 |
| elapsed seconds | 168.893 |
| Sony policies | 10 |
| Sony base-step samples | 24 |
| Nikon offset policies | 7 |
| Nikon global-bias values | 7 |
| Nikon GTLI/Bp/Br rows | 20 |
| total candidate rows | 29280 |

主要文件：

- `point_cloud_candidates.csv`
- `point_cloud_summary.csv`
- `actual_bpp_bin_per_scene.csv`
- `actual_bpp_bin_envelope_summary.csv`
- `fig_bpp_quality_point_cloud.png`
- `fig_bpp_quality_envelope.png`
- `fig_bpp_quality_hexbin.png`

LaTeX 图已复制到：

- `docs/proxy-four-plane-latex-report/figures/fig_exhaustive_bpp_quality_point_cloud.png`
- `docs/proxy-four-plane-latex-report/figures/fig_exhaustive_bpp_quality_envelope.png`
- `docs/proxy-four-plane-latex-report/figures/fig_exhaustive_bpp_quality_hexbin.png`

## 全局点云范围

| codec | candidate rows | actual bpp range | PSNR range | median scene PSNR frontier count |
|---|---:|---:|---:|---:|
| Sony #824 | 5760 | 0.242--9.111 | -0.578--90.198 dB | 74.0 |
| Nikon #826 | 23520 | 0.151--10.007 | 46.502--82.530 dB | 21.5 |

解释：

- Sony 的 base step 是连续控制量，本次用 24 个对数采样点，所以点云横向更连续。
- Nikon 的 GTLI/Bp/Br 是台阶控制量，点更多但有效 frontier 更短。
- 极低 bpp 或极高 bpp 的最大/最小 PSNR 包含部分退化或近无损点，不能只看全局极值；正式比较应看 actual-bpp bins 和 paired coverage。

## actual-bpp bin 上包络

每个 bin 内先在每个 scene×codec 中取 best PSNR，再对覆盖场景做中位数。关键整数 bpp bins：

| actual bpp bin | Sony covered scenes | Sony median best PSNR | Nikon covered scenes | Nikon median best PSNR |
|---:|---:|---:|---:|---:|
| 1.5 | 24 | 56.754 dB | 11 | 63.344 dB |
| 2.0 | 24 | 60.874 dB | 12 | 64.015 dB |
| 2.5 | 24 | 63.678 dB | 12 | 63.430 dB |
| 3.0 | 23 | 65.725 dB | 14 | 66.560 dB |
| 4.0 | 15 | 67.304 dB | 11 | 62.753 dB |
| 5.0 | 11 | 71.122 dB | 9 | 66.283 dB |

这张表和前面的 bootstrap envelope 不完全一样，因为它使用的是 current-policy dense 点云，而不是旧 2448 点 policy sweep。它给出的更直观形状是：

1. 低码率 1.5--2.0 bpp：Nikon 当前可见 GTLI policy 在覆盖到的场景上中位 best PSNR 更高，但覆盖场景较少。
2. 2.5--3.0 bpp：两者接近，Nikon 在 3.0 bin 中位略高，Sony 覆盖更满。
3. 4.0--5.0 bpp：Sony current-policy dense 上包络明显更高；这说明 Sony 不是“高码率一定平台化”，而是 canonical 路径平台化，换 selector/base-step policy 后仍有更高 quality-rate 点。

## 方法边界

这不是数学意义上的无穷连续穷尽：

1. Sony `base_step` 连续，本次是 24 点对数网格采样。
2. Sony row-cycle policy 空间没有完全穷举，只枚举当前定义的 10 个 policy。
3. Nikon 当前全量是 7 个 offset policy、7 个 global bias、20 个 GTLI/Bp/Br rows；更宽的 `[-1,0,1]^4` offset grid 脚本已支持，但计算成本更高。
4. 为加速，Nikon 具有相同四组件 effective GTLI tuple 的组合复用第一个重建和 syntax row。因此这是 effective reconstruction cloud，不是逐个冗余 header-bit 组合的完全语法枚举。
5. 所有结果仍只代表 decoder-visible syntax/math，不等于 Sony/Nikon production encoder 私有 RDO。

## 后续更宽网格入口

更宽网格可以这样跑：

```powershell
python tools\exhaustive_bpp_quality_cloud.py `
  --out-dir out\exhaustive_bpp_quality_cloud_wide_20260605 `
  --sony-policy-mode current-plus-maps `
  --sony-tie-rb `
  --sony-base-step-count 24 `
  --nikon-offset-mode current-plus-grid `
  --nikon-offset-values -1,0,1 `
  --nikon-bias-count 7 `
  --jobs 16 `
  --max-scatter-points 180000
```

预计候选点约 338304。它会更接近“尽可能遍历”，但仍是有限网格，不是 production RDO 穷举。
