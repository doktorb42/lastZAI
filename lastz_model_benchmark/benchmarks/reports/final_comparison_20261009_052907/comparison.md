# Last Z performance and reliability benchmark

Run: 2026-10-09T05:29:07+02:00

This benchmark uses synthetic fixtures only. It never invokes ADB, BlueStacks, screenshots, or real game actions.

## Backend inventory

| Requested backend | Status | Device / reason |
| --- | --- | --- |
| vulkan | available | device detected: NVIDIA GeForce RTX 3050 (8256 MiB, 7485 MiB free) |
| cuda | unavailable | backend device absent from --list-devices |

## Action suite — warm randomized samples

A production action configuration must have 100% JSON/schema/legal/exact rates, verified backend evidence, no errors, and VRAM headroom.

| Model | Backend | Ctx | Cache | Spec | JSON | Legal | Exact | E2E p50 / p95 / p99 | Gen tok/s p50 | Peak headroom |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| qwen2.5-3b-instruct-q4-k-m | vulkan | 1024 | disabled | off | 100.0% | 88.2% | 61.8% | 240.91 / 404.34 / 449.21 ms | 94.87 | 5282.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | vulkan | 2048 | disabled | off | 100.0% | 88.2% | 61.8% | 237.57 / 404.35 / 448.25 ms | 94.56 | 5253.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | vulkan | 4096 | disabled | off | 100.0% | 88.2% | 61.8% | 241.63 / 409.38 / 451.37 ms | 94.68 | 5177.00 MiB |
| qwen3-1.7b-q4-k-m | vulkan | 1024 | disabled | off | 97.1% | 88.2% | 52.9% | 228.28 / 388.87 / 978.37 ms | 95.98 | 6001.00 MiB |
| qwen3-1.7b-q4-k-m | vulkan | 2048 | disabled | off | 97.1% | 88.2% | 52.9% | 228.46 / 391.95 / 981.89 ms | 95.69 | 5886.00 MiB |
| qwen3-1.7b-q4-k-m | vulkan | 4096 | disabled | off | 97.1% | 88.2% | 52.9% | 229.23 / 394.64 / 976.11 ms | 95.61 | 5667.00 MiB |
| qwen3-4b-q4-k-m | vulkan | 1024 | disabled | off | 97.1% | 88.2% | 73.5% | 390.99 / 630.21 / 1841.46 ms | 62.97 | 4641.00 MiB |
| qwen3-4b-q4-k-m | vulkan | 2048 | disabled | off | 97.1% | 88.2% | 73.5% | 393.89 / 655.47 / 1848.90 ms | 62.45 | 4495.00 MiB |
| qwen3-4b-q4-k-m | vulkan | 4096 | disabled | off | 97.1% | 88.2% | 73.5% | 394.99 / 633.51 / 1852.23 ms | 62.84 | 4209.00 MiB |
| qwen3-4b-q4-k-m | vulkan | 2048 | disabled | ngram-cache | 97.1% | 88.2% | 73.5% | 454.77 / 677.99 / 1077.19 ms | 50.84 | 4497.00 MiB |
| qwen3-4b-q4-k-m | vulkan | 1024 | enabled | off | 97.1% | 88.2% | 73.5% | 260.78 / 503.27 / 1695.18 ms | 62.62 | 4633.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | vulkan | 1024 | enabled | off | 100.0% | 88.2% | 61.8% | 146.49 / 305.81 / 351.99 ms | 94.73 | 5287.00 MiB |

## Chat suite — quality-first comparison

Quality is a deterministic fixture rubric (required concepts, requested length, natural sentence structure, character cap, and safety constraints). Raw replies are retained in the JSON report for manual review.

| Model | Backend | Ctx | Cache | Mode | Quality mean / min | Pass rate | TTFT p50 / p95 | Gen tok/s p50 | E2E p95 | Headroom |
| --- | --- | ---: | --- | --- | ---: | ---: | --- | ---: | ---: | ---: |
| qwen2.5-3b-instruct-q4-k-m | vulkan | 1024 | disabled | streaming | 77.17 / 60.00 | 0.0% | 96.40 / 104.06 ms | 85.23 | 803.34 ms | 5295.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | vulkan | 1024 | disabled | non_streaming | 77.17 / 60.00 | 0.0% | n/a / n/a ms | 85.23 | 812.24 ms | 5295.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | vulkan | 2048 | disabled | streaming | 77.17 / 60.00 | 0.0% | 90.11 / 100.00 ms | 85.17 | 803.01 ms | 5258.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | vulkan | 2048 | disabled | non_streaming | 77.17 / 60.00 | 0.0% | n/a / n/a ms | 85.17 | 795.87 ms | 5258.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | vulkan | 4096 | disabled | streaming | 77.17 / 60.00 | 0.0% | 91.51 / 102.89 ms | 84.62 | 808.83 ms | 5181.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | vulkan | 4096 | disabled | non_streaming | 77.17 / 60.00 | 0.0% | n/a / n/a ms | 85.08 | 817.42 ms | 5181.00 MiB |
| qwen3-1.7b-q4-k-m | vulkan | 1024 | disabled | streaming | 76.73 / 52.86 | 16.7% | 65.97 / 72.80 ms | 139.00 | 484.17 ms | 6006.00 MiB |
| qwen3-1.7b-q4-k-m | vulkan | 1024 | disabled | non_streaming | 76.73 / 52.86 | 16.7% | n/a / n/a ms | 139.42 | 484.48 ms | 6006.00 MiB |
| qwen3-1.7b-q4-k-m | vulkan | 2048 | disabled | streaming | 76.73 / 52.86 | 16.7% | 70.46 / 83.61 ms | 139.44 | 487.33 ms | 5891.00 MiB |
| qwen3-1.7b-q4-k-m | vulkan | 2048 | disabled | non_streaming | 76.73 / 52.86 | 16.7% | n/a / n/a ms | 139.90 | 503.35 ms | 5891.00 MiB |
| qwen3-1.7b-q4-k-m | vulkan | 4096 | disabled | streaming | 76.73 / 52.86 | 16.7% | 66.75 / 71.75 ms | 139.02 | 479.51 ms | 5671.00 MiB |
| qwen3-1.7b-q4-k-m | vulkan | 4096 | disabled | non_streaming | 76.73 / 52.86 | 16.7% | n/a / n/a ms | 139.44 | 470.26 ms | 5671.00 MiB |
| qwen3-4b-q4-k-m | vulkan | 1024 | disabled | streaming | 86.39 / 68.67 | 16.7% | 128.19 / 133.77 ms | 66.33 | 2236.80 ms | 4646.00 MiB |
| qwen3-4b-q4-k-m | vulkan | 1024 | disabled | non_streaming | 86.39 / 68.67 | 16.7% | n/a / n/a ms | 66.38 | 2216.98 ms | 4646.00 MiB |
| qwen3-4b-q4-k-m | vulkan | 2048 | disabled | streaming | 86.39 / 68.67 | 16.7% | 134.10 / 156.15 ms | 66.34 | 2257.03 ms | 4503.00 MiB |
| qwen3-4b-q4-k-m | vulkan | 2048 | disabled | non_streaming | 86.39 / 68.67 | 16.7% | n/a / n/a ms | 66.39 | 2240.69 ms | 4503.00 MiB |
| qwen3-4b-q4-k-m | vulkan | 4096 | disabled | streaming | 86.39 / 68.67 | 16.7% | 129.99 / 141.09 ms | 65.77 | 2255.89 ms | 4213.00 MiB |
| qwen3-4b-q4-k-m | vulkan | 4096 | disabled | non_streaming | 86.39 / 68.67 | 16.7% | n/a / n/a ms | 66.12 | 2231.67 ms | 4213.00 MiB |
| qwen3-4b-q4-k-m | vulkan | 1024 | enabled | streaming | 87.67 / 70.71 | 16.7% | 55.28 / 71.61 ms | 66.58 | 2257.72 ms | 4644.00 MiB |
| qwen3-4b-q4-k-m | vulkan | 1024 | enabled | non_streaming | 85.28 / 68.67 | 16.7% | n/a / n/a ms | 66.65 | 2204.58 ms | 4644.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | vulkan | 1024 | enabled | streaming | 76.84 / 60.00 | 0.0% | 30.55 / 75.10 ms | 85.15 | 740.28 ms | 5292.00 MiB |
| qwen2.5-3b-instruct-q4-k-m | vulkan | 1024 | enabled | non_streaming | 75.59 / 60.00 | 0.0% | n/a / n/a ms | 86.08 | 761.72 ms | 5292.00 MiB |

## Detailed warm distributions

Each distribution is p50 / p95 / p99 / max; latency is milliseconds and throughput is tokens/s. A non-streaming response has no observable first token, so TTFT is n/a.

| Suite | Model | Backend | Ctx | Cache | Mode | Cold E2E | TTFT | E2E | Prompt processing | Generation |
| --- | --- | --- | ---: | --- | --- | ---: | --- | --- | --- | --- |
| action | qwen2.5-3b-instruct-q4-k-m | vulkan | 1024 | disabled | non-streaming | 235.80 | n/a | 240.91 / 404.34 / 449.21 / 451.25 | 2585.09 / 2732.80 / 2746.00 / 2771.26 | 94.87 / 99.20 / 99.38 / 99.54 |
| action | qwen2.5-3b-instruct-q4-k-m | vulkan | 2048 | disabled | non-streaming | 250.67 | n/a | 237.57 / 404.35 / 448.25 / 452.22 | 2570.62 / 2730.26 / 2751.92 / 2792.95 | 94.56 / 98.81 / 99.08 / 99.29 |
| action | qwen2.5-3b-instruct-q4-k-m | vulkan | 4096 | disabled | non-streaming | 242.23 | n/a | 241.63 / 409.38 / 451.37 / 464.01 | 2577.56 / 2722.66 / 2742.16 / 2780.19 | 94.68 / 98.78 / 98.83 / 98.84 |
| chat | qwen2.5-3b-instruct-q4-k-m | vulkan | 1024 | disabled | non_streaming | 323.14 | n/a | 617.61 / 812.24 / 844.88 / 853.04 | 1965.60 / 2064.30 / 2085.40 / 2090.68 | 85.23 / 88.11 / 88.19 / 88.21 |
| chat | qwen2.5-3b-instruct-q4-k-m | vulkan | 1024 | disabled | streaming | 323.14 | 96.40 / 104.06 / 104.42 / 104.52 | 625.04 / 803.34 / 830.70 / 837.54 | 1985.24 / 2100.45 / 2101.86 / 2102.22 | 85.23 / 87.97 / 87.97 / 87.98 |
| chat | qwen2.5-3b-instruct-q4-k-m | vulkan | 2048 | disabled | non_streaming | 307.82 | n/a | 622.73 / 795.87 / 823.98 / 831.01 | 1962.29 / 2083.81 / 2089.70 / 2091.18 | 85.17 / 87.74 / 87.88 / 87.92 |
| chat | qwen2.5-3b-instruct-q4-k-m | vulkan | 2048 | disabled | streaming | 307.82 | 90.11 / 100.00 / 101.70 / 102.12 | 624.23 / 803.01 / 831.49 / 838.61 | 1973.64 / 2062.37 / 2082.21 / 2087.17 | 85.17 / 87.88 / 87.89 / 87.89 |
| chat | qwen2.5-3b-instruct-q4-k-m | vulkan | 4096 | disabled | non_streaming | 332.50 | n/a | 622.05 / 817.42 / 849.61 / 857.65 | 1951.10 / 1981.61 / 1982.36 / 1982.54 | 85.08 / 87.41 / 87.44 / 87.44 |
| chat | qwen2.5-3b-instruct-q4-k-m | vulkan | 4096 | disabled | streaming | 332.50 | 91.51 / 102.89 / 105.72 / 106.43 | 624.83 / 808.83 / 835.75 / 842.48 | 1979.09 / 2083.96 / 2086.90 / 2087.63 | 84.62 / 87.56 / 87.78 / 87.84 |
| action | qwen3-1.7b-q4-k-m | vulkan | 1024 | disabled | non-streaming | 212.46 | n/a | 228.28 / 388.87 / 978.37 / 978.41 | 3627.66 / 3903.63 / 3949.87 / 4028.83 | 95.98 / 112.82 / 120.12 / 121.11 |
| action | qwen3-1.7b-q4-k-m | vulkan | 2048 | disabled | non-streaming | 211.09 | n/a | 228.46 / 391.95 / 981.89 / 983.71 | 3617.50 / 3890.21 / 3930.95 / 3985.89 | 95.69 / 110.95 / 119.61 / 120.39 |
| action | qwen3-1.7b-q4-k-m | vulkan | 4096 | disabled | non-streaming | 210.53 | n/a | 229.23 / 394.64 / 976.11 / 990.38 | 3674.90 / 3885.07 / 3946.15 / 3982.45 | 95.61 / 112.05 / 120.48 / 120.55 |
| chat | qwen3-1.7b-q4-k-m | vulkan | 1024 | disabled | non_streaming | 183.26 | n/a | 351.67 / 484.48 / 489.16 / 490.33 | 3405.05 / 3614.22 / 3618.61 / 3619.70 | 139.42 / 141.40 / 141.63 / 141.68 |
| chat | qwen3-1.7b-q4-k-m | vulkan | 1024 | disabled | streaming | 183.26 | 65.97 / 72.80 / 74.33 / 74.71 | 349.48 / 484.17 / 493.31 / 495.59 | 3448.06 / 3610.36 / 3617.31 / 3619.04 | 139.00 / 142.73 / 143.14 / 143.25 |
| chat | qwen3-1.7b-q4-k-m | vulkan | 2048 | disabled | non_streaming | 196.80 | n/a | 348.04 / 503.35 / 511.56 / 513.62 | 3447.87 / 3614.10 / 3621.43 / 3623.26 | 139.90 / 143.52 / 144.11 / 144.25 |
| chat | qwen3-1.7b-q4-k-m | vulkan | 2048 | disabled | streaming | 196.80 | 70.46 / 83.61 / 85.55 / 86.04 | 346.65 / 487.33 / 494.72 / 496.57 | 3447.80 / 3621.30 / 3623.23 / 3623.72 | 139.44 / 143.20 / 143.84 / 144.00 |
| chat | qwen3-1.7b-q4-k-m | vulkan | 4096 | disabled | non_streaming | 181.15 | n/a | 336.64 / 470.26 / 474.43 / 475.47 | 3417.42 / 3614.41 / 3619.26 / 3620.47 | 139.44 / 143.39 / 143.98 / 144.13 |
| chat | qwen3-1.7b-q4-k-m | vulkan | 4096 | disabled | streaming | 181.15 | 66.75 / 71.75 / 71.94 / 71.98 | 340.23 / 479.51 / 486.94 / 488.80 | 3432.59 / 3599.83 / 3605.34 / 3606.72 | 139.02 / 142.94 / 143.52 / 143.66 |
| action | qwen3-4b-q4-k-m | vulkan | 1024 | disabled | non-streaming | 400.15 | n/a | 390.99 / 630.21 / 1841.46 / 1842.09 | 1840.96 / 1927.46 / 1939.43 / 1956.67 | 62.97 / 64.26 / 69.58 / 69.96 |
| action | qwen3-4b-q4-k-m | vulkan | 2048 | disabled | non-streaming | 391.23 | n/a | 393.89 / 655.47 / 1848.90 / 1851.91 | 1811.78 / 1917.78 / 1927.17 / 1939.98 | 62.45 / 64.17 / 70.49 / 70.54 |
| action | qwen3-4b-q4-k-m | vulkan | 4096 | disabled | non-streaming | 391.88 | n/a | 394.99 / 633.51 / 1852.23 / 1858.25 | 1821.35 / 1917.50 / 1923.61 / 1928.58 | 62.84 / 64.20 / 69.66 / 70.55 |
| chat | qwen3-4b-q4-k-m | vulkan | 1024 | disabled | non_streaming | 449.13 | n/a | 1213.87 / 2216.98 / 2302.29 / 2323.62 | 1599.89 / 1682.09 / 1683.15 / 1683.42 | 66.38 / 68.09 / 68.30 / 68.35 |
| chat | qwen3-4b-q4-k-m | vulkan | 1024 | disabled | streaming | 449.13 | 128.19 / 133.77 / 134.18 / 134.28 | 1215.71 / 2236.80 / 2329.06 / 2352.13 | 1600.65 / 1682.32 / 1686.87 / 1688.01 | 66.33 / 67.92 / 68.10 / 68.14 |
| chat | qwen3-4b-q4-k-m | vulkan | 2048 | disabled | non_streaming | 436.94 | n/a | 1233.57 / 2240.69 / 2329.50 / 2351.71 | 1604.15 / 1687.46 / 1688.84 / 1689.19 | 66.39 / 68.18 / 68.38 / 68.43 |
| chat | qwen3-4b-q4-k-m | vulkan | 2048 | disabled | streaming | 436.94 | 134.10 / 156.15 / 157.19 / 157.46 | 1221.28 / 2257.03 / 2352.51 / 2376.38 | 1598.23 / 1672.92 / 1678.48 / 1679.87 | 66.34 / 68.11 / 68.44 / 68.52 |
| chat | qwen3-4b-q4-k-m | vulkan | 4096 | disabled | non_streaming | 437.21 | n/a | 1227.47 / 2231.67 / 2317.19 / 2338.57 | 1565.60 / 1646.46 / 1648.37 / 1648.84 | 66.12 / 68.01 / 68.37 / 68.46 |
| chat | qwen3-4b-q4-k-m | vulkan | 4096 | disabled | streaming | 437.21 | 129.99 / 141.09 / 142.00 / 142.22 | 1213.15 / 2255.89 / 2351.49 / 2375.39 | 1560.72 / 1637.54 / 1638.96 / 1639.31 | 65.77 / 68.06 / 68.38 / 68.45 |
| action | qwen3-4b-q4-k-m | vulkan | 2048 | disabled | non-streaming | 433.25 | n/a | 454.77 / 677.99 / 1077.19 / 1079.02 | 1821.31 / 1910.10 / 1921.80 / 1936.52 | 50.84 / 58.75 / 133.39 / 133.49 |
| action | qwen3-4b-q4-k-m | vulkan | 1024 | enabled | non-streaming | 385.59 | n/a | 260.78 / 503.27 / 1695.18 / 1695.72 | 982.39 / 1321.19 / 1809.02 / 1817.15 | 62.62 / 64.42 / 70.41 / 70.56 |
| chat | qwen3-4b-q4-k-m | vulkan | 1024 | enabled | non_streaming | 433.19 | n/a | 1169.00 / 2204.58 / 2300.51 / 2324.50 | 992.41 / 1354.55 / 1374.85 / 1379.92 | 66.65 / 68.97 / 69.36 / 69.46 |
| chat | qwen3-4b-q4-k-m | vulkan | 1024 | enabled | streaming | 433.19 | 55.28 / 71.61 / 74.54 / 75.28 | 1143.06 / 2257.72 / 2322.22 / 2338.34 | 64.15 / 788.01 / 981.04 / 1029.29 | 66.58 / 68.10 / 68.42 / 68.50 |
| action | qwen2.5-3b-instruct-q4-k-m | vulkan | 1024 | enabled | non-streaming | 236.65 | n/a | 146.49 / 305.81 / 351.99 / 352.44 | 1464.82 / 1839.28 / 2505.31 / 2598.59 | 94.73 / 99.13 / 99.48 / 99.59 |
| chat | qwen2.5-3b-instruct-q4-k-m | vulkan | 1024 | enabled | non_streaming | 306.06 | n/a | 447.11 / 761.72 / 800.34 / 809.99 | 521.05 / 1468.01 / 1561.90 / 1585.37 | 86.08 / 87.60 / 87.71 / 87.73 |
| chat | qwen2.5-3b-instruct-q4-k-m | vulkan | 1024 | enabled | streaming | 306.06 | 30.55 / 75.10 / 78.05 / 78.79 | 574.04 / 740.28 / 760.72 / 765.83 | 81.80 / 1351.31 / 1393.74 / 1404.35 | 85.15 / 88.67 / 88.71 / 88.72 |

## VRAM and verified runtime backend

VRAM values are MiB from nvidia-smi; minimum free is sampled while requests are running. Backend is accepted only when the server log proves the loaded backend, selected device, and GPU layer offload.

| Suite | Model | Ctx | Before load used | After load used | Peak during | Minimum free | Logged device | Offload | Verified |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| action | qwen2.5-3b-instruct-q4-k-m | 1024 | 708.00 | 2893.00 | 2910.00 | 5282.00 | Vulkan0 | 37/37 | yes |
| action | qwen2.5-3b-instruct-q4-k-m | 2048 | 707.00 | 2917.00 | 2939.00 | 5253.00 | Vulkan0 | 37/37 | yes |
| action | qwen2.5-3b-instruct-q4-k-m | 4096 | 703.00 | 2998.00 | 3015.00 | 5177.00 | Vulkan0 | 37/37 | yes |
| chat | qwen2.5-3b-instruct-q4-k-m | 1024 | 707.00 | 2881.00 | 2897.00 | 5295.00 | Vulkan0 | 37/37 | yes |
| chat | qwen2.5-3b-instruct-q4-k-m | 2048 | 707.00 | 2922.00 | 2934.00 | 5258.00 | Vulkan0 | 37/37 | yes |
| chat | qwen2.5-3b-instruct-q4-k-m | 4096 | 708.00 | 2999.00 | 3011.00 | 5181.00 | Vulkan0 | 37/37 | yes |
| action | qwen3-1.7b-q4-k-m | 1024 | 709.00 | 2179.00 | 2191.00 | 6001.00 | Vulkan0 | 29/29 | yes |
| action | qwen3-1.7b-q4-k-m | 2048 | 707.00 | 2293.00 | 2306.00 | 5886.00 | Vulkan0 | 29/29 | yes |
| action | qwen3-1.7b-q4-k-m | 4096 | 707.00 | 2513.00 | 2525.00 | 5667.00 | Vulkan0 | 29/29 | yes |
| chat | qwen3-1.7b-q4-k-m | 1024 | 707.00 | 2177.00 | 2186.00 | 6006.00 | Vulkan0 | 29/29 | yes |
| chat | qwen3-1.7b-q4-k-m | 2048 | 703.00 | 2293.00 | 2301.00 | 5891.00 | Vulkan0 | 29/29 | yes |
| chat | qwen3-1.7b-q4-k-m | 4096 | 706.00 | 2509.00 | 2521.00 | 5671.00 | Vulkan0 | 29/29 | yes |
| action | qwen3-4b-q4-k-m | 1024 | 707.00 | 3531.00 | 3551.00 | 4641.00 | Vulkan0 | 37/37 | yes |
| action | qwen3-4b-q4-k-m | 2048 | 707.00 | 3679.00 | 3697.00 | 4495.00 | Vulkan0 | 37/37 | yes |
| action | qwen3-4b-q4-k-m | 4096 | 707.00 | 3963.00 | 3983.00 | 4209.00 | Vulkan0 | 37/37 | yes |
| chat | qwen3-4b-q4-k-m | 1024 | 702.00 | 3535.00 | 3546.00 | 4646.00 | Vulkan0 | 37/37 | yes |
| chat | qwen3-4b-q4-k-m | 2048 | 706.00 | 3679.00 | 3689.00 | 4503.00 | Vulkan0 | 37/37 | yes |
| chat | qwen3-4b-q4-k-m | 4096 | 702.00 | 3968.00 | 3979.00 | 4213.00 | Vulkan0 | 37/37 | yes |
| action | qwen3-4b-q4-k-m | 2048 | 702.00 | 3679.00 | 3695.00 | 4497.00 | Vulkan0 | 37/37 | yes |
| action | qwen3-4b-q4-k-m | 1024 | 712.00 | 3547.00 | 3559.00 | 4633.00 | Vulkan0 | 37/37 | yes |
| chat | qwen3-4b-q4-k-m | 1024 | 709.00 | 3538.00 | 3548.00 | 4644.00 | Vulkan0 | 37/37 | yes |
| action | qwen2.5-3b-instruct-q4-k-m | 1024 | 709.00 | 2883.00 | 2905.00 | 5287.00 | Vulkan0 | 37/37 | yes |
| chat | qwen2.5-3b-instruct-q4-k-m | 1024 | 709.00 | 2887.00 | 2900.00 | 5292.00 | Vulkan0 | 37/37 | yes |

## Recommended production configuration

No action configuration qualified. Keep the deterministic agent path until a candidate passes every safety gate.

### English private chat

```json
{
  "model": "qwen3-4b-q4-k-m",
  "backend": "vulkan",
  "context": 1024,
  "cache_mode": "enabled",
  "speculative": "off",
  "gpu_layers_requested": 99,
  "threads": 16,
  "vram_headroom_mib": 4644.0,
  "backend_evidence": {
    "loaded_backends": [
      "CPU",
      "RPC",
      "Vulkan"
    ],
    "selected_devices": [
      "Vulkan0"
    ],
    "offload": [
      {
        "offloaded": 37,
        "total": 37
      }
    ],
    "buffers": [
      {
        "device": "Vulkan0",
        "kind": "model",
        "mib": 2375.91
      },
      {
        "device": "Vulkan0",
        "kind": "kv",
        "mib": 144.0
      },
      {
        "device": "Vulkan0",
        "kind": "compute",
        "mib": 301.75
      }
    ],
    "context_values": [
      "1024",
      "1024"
    ],
    "speculative_mentions": [
      "no implementations specified for speculative decoding",
      "slot   load_model: id  0 | task -1 | speculative decoding context not initialized"
    ],
    "verified": true,
    "reason": "verified selected device and GPU layer offload"
  }
}
```

## Reliability notes

- Schema contract vectors: 17/17 passed.
- Cold/first-hit requests are kept out of warm p50/p95/p99 distributions and stored in the JSON report.
- Streaming TTFT is measured at the first non-empty SSE content delta. Non-streaming responses intentionally report TTFT as n/a rather than fabricating it.
- Backend labels are accepted only when the server log proves the requested device and GPU layer offload.

Full raw data, replies, timings, and per-configuration server-log paths: `comparison.json`. Direct benchmark runs keep their logs in `logs/`.
