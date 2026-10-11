# 工作負載與需求來源

| 項目 | 原始證據 |
|---|---|
| Native CUDA primitives 與控制檔投影 | `experiments/kubeflow_dra/gpu_workload.py`；本實驗的 `workload/gpu_primitives.py` 保留其核心程式，`native_workload.py` 呼叫原 entry point |
| Capacity CUDA 工作負載與生命週期行為 | `experiments/kubeflow_dra_e4b/gpu_workload.py`；本實驗的 `workload/gpu_workload.py` 保留原程式 |
| PyTorch 映像檔 digest | `experiments/kubeflow_dra/config/images.json` 的 `images.pytorch` |
| CPU、memory、downward API、security context 與 uncached CUDA allocator | `experiments/kubeflow_dra/pipeline.py` 與 `experiments/kubeflow_dra_e4b/pipeline.py` |
| Native attribute 與 nonexistent selector | `experiments/kubeflow_dra/runner.py` 的 `stage2`；architecture 為 `Ampere`，無匹配 UUID 為全零 |
| 原 GPU productName 與 compute capability | E4 `stage2/attribute-r1/resourceslices.yaml`；A40 為 `NVIDIA A40`、`8.6.0`，RTX 4090 為 `NVIDIA GeForce RTX 4090`、`8.9.0` |
| H3-H4 與 H5 holder 需求 | `EXPERIMENT_4B_CONFIGURATIONS.md` 的 `heterogeneous-capacity/h3-h4-r1/resourceclaimtemplates-input.yaml` 與 `heterogeneous-capacity/h5-r1/resourceclaimtemplates-input.yaml` |
| Holder 即時容量公式 | `experiments/kubeflow_dra_e4b/design.py` 的 `holder_requests` |
| Provider 映像檔、host 檔案與還原檢查 | [第四章 HAMi provider](../../04-isolation/hami-dra/README.md)及[原生 provider 切換](../../04-isolation/provider/README.md) |

原始物件使用實驗 run-id 與 Task 編號，本套件以 Job 角色命名；各 worker 仍各有獨立 Claim。Native A40／4090 與 holder 的 selector 由固定 GPU UUID 改為相同 GPU 的 `productName`，不指定節點；原 `attribute` 情境仍只使用 architecture selector。每輪採用的完整輸入、即時發布容量與實際 holder 值都保留在 `out/`。
