# LibRaw #826 Nikon HE Static Safety Review

Date: 2026-06-01

Scope: static review of LibRaw/LibRaw#826 at head `8aebd05d0f0378dcd5df2bc4cefb0f7104aa6ee3`, local clone `C:\Users\姜尧耕\AppData\Local\Temp\libraw_pr826_nikon_he`. This is not a fuzzing result and not a reference-output conformance result. It is the allocation, overflow, bounds, failure-semantics, and cancel audit that image-codec papers usually group under complexity and robustness.

Primary PR source: <https://github.com/LibRaw/LibRaw/pull/826>

Comparison baseline: #824 Sony ARW6 CRAW HQ at `1fa7855c` has explicit strip/tile/packet guards, a `max_raw_memory_mb` working-set estimate, and `checkCancel()` in stream/row loops. #826 should be judged by the same merge-readiness standard.

## Summary Findings

| ID | Severity | Area | Finding | Evidence | Suggested action |
|---|---|---|---|---|---|
| NHE-S1 | High | malformed/truncated stream semantics | A truncated precinct walk can silently produce a partially decoded or zero-filled image instead of a LibRaw exception. `decode_nikon_he_image()` breaks on short precincts/sentinel, `decode_tile()` can mark success for fewer than 18 precincts, and `nikon_he_load_raw()` then `memset(raw_image, 0)` and returns on decode failure. | `nikon_he_decode.cpp:89-107`, `126-138`; `nikon_he_tile.cpp:150-158`, `224`; `nikon_he_decoder.cpp:108-115` | Treat malformed/short precinct streams as `LIBRAW_EXCEPTION_IO_CORRUPT`/`DECODE_RAW`; require expected precinct counts for non-tail tiles; never return a black raw image as if decode succeeded. |
| NHE-S2 | High | bounds/overflow | GCLI prediction LUT indexing is not bounded. `read_unary()` may return values above 31 on corrupt or unterminated streams, and `lookup_prediction()` directly indexes an 8192-entry table with `unary_code`, `gtli`, and `previous_gcli`. | `nikon_he_gcli_decode.cpp:81-89`; `nikon_he_bit_reader.cpp:85-91`; `nikon_he_predict_lut.h:51-56` | Clamp or reject `unary_code >= 32`, `gtli` outside 0..15, and previous GCLI outside 0..15; propagate decode failure rather than using sentinel values as data. |
| NHE-S3 | High | memory | The decoder has no `imgdata.rawparams.max_raw_memory_mb` gate and allocates multiple full-image or all-tile working buffers at once. A Z8/Z9 FF HE sample statically estimates about 566 MB peak before tile-local vectors and predecessor state, including LibRaw `raw_image`, `precinct_bytes`, `bayer`, `tile_coeff_buf`, and `step1_scratch`. | `nikon_he_decoder.cpp:73-100`; `nikon_he_decode.cpp:76-83`, `109-112`; estimate below | Add a checked 64-bit working-set estimate tied to `max_raw_memory_mb`; reject dimensions/strip sizes that exceed it; consider processing/copying one tile stage at a time if possible. |
| NHE-S4 | High | memory leak | `PrecinctPredecessorState::init()` allocates per-tile GCLI buffers, but `destroy()` is never called and there is no destructor. The object is created inside the tile loop, so the leak repeats once per tile. | `nikon_he_decode.cpp:118-120`; `nikon_he_predecessor.cpp:29-54`, `80-94`; `nikon_he_predecessor.h:48-58` | Add RAII ownership, a destructor, or an explicit `pred_state.destroy()` on all tile-loop paths. |
| NHE-S5 | Medium | allocation overflow | Several allocation sizes multiply `int`-derived dimensions after only loose validation. They use `size_t` casts, but there is no explicit positive upper bound, overflow check, or LibRaw allocation exception mapping. | `nikon_he_decoder.cpp:52-56`, `100`; `nikon_he_decode.cpp:76-83`, `109-112`; `nikon_he_tile.cpp:91-114` | Validate `raw_width`, `raw_height`, `n_tiles`, `stripe_ints`, and all products with 64-bit intermediates before allocation; map allocation failure to `LIBRAW_EXCEPTION_ALLOC`. |
| NHE-S6 | Medium | fixed container offset | The precinct stream is assumed to start at `strip_offset + 0x9b`. The current code checks `strip_size > 0x9b`, but does not check `data_offset + 0x9b` overflow or parse the skipped prefix. | `nikon_he_decoder.cpp:61-76` | Check offset addition with 64-bit bounds against stream/file size; record prefix marker/version facts in probe output; fail explicitly if the prefix layout is not recognized. |
| NHE-S7 | Medium | cancel behavior | No `checkCancel()` appears in the Nikon HE load path or inner precinct/tile/row loops. Large FF files can run through precinct decode, two image-wide passes, and Bayer output without honoring user cancellation. | static `rg "checkCancel"` over `src/decoders/nikon_he_decoder.cpp src/decoders/nikon_he` returned none | Add cancellation checks in the file-precinct walk, tile decode loop, pass 2, pass 3, and long row loops. |
| NHE-S8 | Medium | bitstream EOF semantics | `BitReader` zero-pads reads after EOF and `read_unary()` returns a count on unterminated EOF. That may be acceptable for trusted substream sizes, but it is risky when corrupt streams should fail deterministically. | `nikon_he_bit_reader.cpp:68-82`, `85-91` | Expose EOF/overread status from `BitReader`, and make `decode_precinct()` fail if a substream exhausts before expected data are consumed. |
| NHE-S9 | Medium | partial-tile/tail bounds | `w_rows` for the last tile is `(image_height % 64) / 2`, which becomes 0 when `image_height % 64 == 1`; non-tail tiles assume 32 stripes. There is no explicit validation that image height is even and compatible with the HE tile model. | `nikon_he_decoder.cpp:52-56`; `nikon_he_decode.cpp:147-149`, `174-176` | Require even image height and validate last-tile row count before output; add a DX-crop HE sample to prove this path. |
| NHE-S10 | Low | thread-safety | `compute_subband_layout()` writes a static `LayoutInfo`, and the returned config points at it. That is fine for one decode in one thread, but not safe if two Nikon HE images with different widths are decoded concurrently through the same process. | `nikon_he_subband_config.cpp:124-132`; `nikon_he_subband_config.h:94-97` | Store `LayoutInfo` in a caller-owned object or document single-width/single-thread assumptions. |

## Allocation Map

| Allocation | Code | Inputs | Current guard | Risk |
|---|---|---|---|---|
| Full precinct stream | `std::vector<uint8_t> precinct_bytes(precinct_size)` in `nikon_he_decoder.cpp:73-78` | TIFF `data_size - 0x9b` | only `data_size > 0x9b` | no memory cap; size_t truncation risk on 32-bit builds; reads whole strip before HE/HE* rejection except first-Bp check happens after read |
| Scratch Bayer output | `std::vector<uint16_t> bayer((size_t)img_w * img_h, 0)` in `nikon_he_decoder.cpp:99-106` | `raw_width * raw_height` | width positive and even; height positive | duplicate of LibRaw `raw_image`; no max memory gate |
| Precinct pointer arrays | `new const uint8_t*[n_file_precincts]`, `new size_t[n_file_precincts]` in `nikon_he_decode.cpp:76-83` | `n_tiles * 16 + 2` | `n_tiles=(height+63)/64` | no overflow or upper bound; partial collection can still continue |
| Image-wide coefficient buffers | `tile_coeff_buf`, `step1_scratch`, `overflow` in `nikon_he_decode.cpp:109-112` | `n_tiles * 32 * stripe_ints` | none beyond image dimensions | biggest working set; not checked against LibRaw raw memory limit |
| Tile-local vectors | `x2_carry`, `x3_carry`, `bufA`, `bufB`, `h_out`, `h_work`, `tile_buf` in `nikon_he_tile.cpp:90-114` | width-derived `lift_st`, `passA_stride`, `stripe_ints` | none explicit | per-tile peak not included in a LibRaw memory budget |
| Per-precinct subband arrays | `gcli_out`, `coeffs` in `nikon_he_precinct_decode.cpp:172-206` | `ng`, `ng*4` | config-derived, no direct corrupt-stream size | many short-lived heap allocations inside hot loop; no RAII if a future helper throws |
| Predecessor state | `new uint8_t[ng]`, rotation buffers in `nikon_he_predecessor.cpp:35-54` | config-derived `ng` | none explicit | currently leaked because `destroy()` is unused |

## Static Memory Estimate

This is a conservative minimum based on the code's visible large buffers. It includes LibRaw's existing `raw_image`, the PR's full `precinct_bytes`, duplicate `bayer`, `tile_coeff_buf`, `step1_scratch`, and `overflow`. It does not include tile-local vectors, per-precinct temporary arrays, STL overhead, or LibRaw metadata.

| Sample shape | n_tiles | stripe_ints | raw_image | precinct_bytes | bayer scratch | tile_coeff_buf | step1_scratch | visible subtotal |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Z8/Z9 FF `8280x5520`, 3 bpp strip | 87 | 17664 | 87.2 MB | 16.3 MB | 87.2 MB | 187.6 MB | 187.6 MB | 566.0 MB |
| Zf FF `6064x4040`, 3 bpp strip | 64 | 12672 | 46.7 MB | 8.8 MB | 46.7 MB | 99.0 MB | 99.0 MB | 300.3 MB |

This explains why #826 should have the same kind of memory gate that #824 added around `raw_bytes + data_size + current tile working memory`. It is not enough to say a normal Z9 strip is about 30 MB, because the decoder expands into multiple all-tile `int32_t` buffers.

## Bounds And Failure-Semantics Checklist

| Check | Current #826 status | Review result |
|---|---|---|
| TIFF dispatch | `tiff.cpp` routes Nikon `Compression=34713` with JPEG XS marker; `nikon_he_load_raw()` refuses DNG and HE* by first `Bp` | plausible for supported HE, but still needs negative samples and a fixed-prefix audit |
| Prefix/offset | fixed `0x9b` skip | should be parsed or validated across Z9/Z8/Zf/Z6III and firmware variants |
| Precinct total size | `sz + 12 > remaining` breaks the walk | should fail hard on required precincts, not silently shorten |
| Alignment pad | skips six bytes when present after every 16th precinct | should verify pad presence and value, or define why it is optional |
| Tile precinct count | middle tiles can receive fewer than 18 precincts after a truncated walk | should require 18 for non-tail tiles; tail behavior should be explicit |
| Output on decode failure | black image and return | should throw, because a black raw is indistinguishable from a successful decode to callers |
| HE* unsupported | first `Bp` outside 4/5 throws before decode | good high-level guard; sample matrix should verify no partial output |
| Negative marker sample | Zf `DSC_0040.NEF` has `Compression=34713` but no JPEG XS marker | should stay outside this decoder; keep as regression sample |

## #824 Comparison Notes

#824's current open item is not a memory-safety finding; it is a readability/integration review item from the maintainer: remove or justify `tiff_ifd[raw].dng_levels` writes in non-DNG Sony dispatch. The safety posture is otherwise stronger than #826 in the dimensions relevant here:

- #824 has a single decoder file with explicit stream directory parsing and tile x/y/w/h bounds.
- #824 added packet row count checks from `coded_height`, plus 64-bit checks for packet byte counts and directory offsets.
- #824 estimates working memory against `imgdata.rawparams.max_raw_memory_mb`.
- #824 decodes/copies one stream tile at a time rather than allocating all tile coefficient planes for the whole image.
- #824 has cancellation checks in long loops.

So the fair review posture is:

1. Ask #826 for allocation/overflow/bounds/cancel cleanup before merge, especially NHE-S1 through NHE-S7.
2. Keep #824's `dng_levels` cleanup as the next code-level PR item.
3. For both PRs, require a reference-output table with common-crop pixel statistics before making any codec-quality claim.

## PR Review Text Draft

```text
I did a static safety pass on the Nikon HE path, using the same criteria we are applying to the Sony ARW6 CRAW HQ decoder: bounded allocation, overflow-safe dimensions, malformed stream behavior, and cancellation.

The main issues I would want to resolve before merge are:

- decode_nikon_he_image() can shorten the precinct walk on truncated data and later nikon_he_load_raw() returns a zero-filled raw_image instead of throwing;
- the GCLI prediction LUT lookup is not bounded for corrupt unary runs / previous GCLI values;
- the decoder allocates full precinct_bytes, duplicate bayer, tile_coeff_buf and step1_scratch without a max_raw_memory_mb check;
- PrecinctPredecessorState::destroy() is never called and there is no destructor, so per-tile GCLI storage leaks;
- no checkCancel() calls appear in the HE pipeline.

This is separate from reference-output correctness. The current public sample matrix has HE success-path samples (Z8/Zf first Bp=4), HE* unsupported samples (Bp=1/2/3), and a negative Compression=34713 sample without the JPEG XS marker, but it still lacks NX/Adobe/LibRaw-private common-crop pixel diff.
```

