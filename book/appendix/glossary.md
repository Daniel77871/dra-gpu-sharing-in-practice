# 附錄 A　名詞對照表

## 本頁要回答的問題

本書使用哪些名詞，它們在哪裡首次說明？本表依 English 欄字母排序，同義詞合併為一個定義；「首次出現」指正文首次解釋的位置，前言與總覽中的略稱不列入。

## 背景原理

API 物件、產品名稱，以及 container、process、time-slicing 等工程常用術語保留英文；GPU 記憶體亦可寫作 VRAM。裝置配置、排程記帳、runtime 配額與實體上限採不同詞彙，避免把管理流程與隔離保證混在一起。

## 結果

| 名詞 | English | 說明 | 首次出現 |
| --- | --- | --- | --- |
| 配置 barrier | allocation barrier | 控制多個 client 完成同一步配置並回報快照後，才進入下一步的同步點。 | [4.3 Scope 的 container 與 Pod 拓撲](../04-isolation/hami-dra.md#scope-的-container-與-pod-拓撲) |
| 配置 probe | allocation probe | 嘗試取得一個新的暫時 tensor，以觀察當下 GPU 記憶體是否足夠。 | [第 3 章 背景原理](../03-oom/README.md#背景原理) |
| attempt | attempt | 工作第一次執行或重試時的一次實際 executor Pod 執行，須以 Pod 與 Claim UID 分別追蹤。 | [6.2 成功、失敗、取消與重試後的釋放](../06-heterogeneous/kubeflow.md#成功失敗取消與重試後的釋放) |
| caching allocator | caching allocator | 保留向 CUDA 取得的記憶體區塊，以便後續 tensor 重用的配置器。 | [第 3 章 背景原理](../03-oom/README.md#背景原理) |
| CEL 表達式 | Common Expression Language (CEL) expression | 以裝置屬性或容量判斷候選裝置是否符合需求的表示式。 | [1.2 背景原理](../01-why-dra/dra-model.md#背景原理) |
| 可消耗容量 | consumable capacity | 同一裝置可供不同配置分攤、由 scheduler 檢查合計需求的容量；容量消耗指其記帳機制。 | [1.2 需求可以如何延伸](../01-why-dra/dra-model.md#需求可以如何延伸) |
| CDI | Container Device Interface (CDI) | 以具名規格描述 container 裝置節點、掛載與其他存取設定的介面。 | [1.2 從需求到 container](../01-why-dra/dra-model.md#從需求到-container) |
| CUDA context | CUDA context | 保存 GPU 執行狀態與相關資源的 context，本身也可能占用 GPU 記憶體。 | [第 3 章 背景原理](../03-oom/README.md#背景原理) |
| CUDA OOM | CUDA out of memory | GPU 記憶體配置 API 因容量不足而回傳的錯誤，可由 process 捕捉。 | [第 3 章 背景原理](../03-oom/README.md#背景原理) |
| CUDA process | CUDA process | 實際建立 CUDA 執行狀態並使用 GPU 的 process，也是本書 MPS 記憶體限制的單位。 | [1.2 Driver 專屬設定](../01-why-dra/dra-model.md#driver-專屬設定) |
| 解除配置 | deallocation | 清除 Claim 的配置結果，使裝置或記帳容量可再次配置；Claim 物件可以仍然存在。 | [1.2 解除配置與刪除的時點](../01-why-dra/dra-model.md#解除配置與刪除的時點) |
| 裝置配置 | device allocation | 選定符合 Claim 需求的裝置並記錄結果，不等於在節點完成裝置準備。 | [1.2 背景原理](../01-why-dra/dra-model.md#背景原理) |
| Device Plugin | Device Plugin | 向 kubelet 註冊裝置資源，公布清單並提供 container 存取設定的節點端 plugin。 | [1.1 背景原理](../01-why-dra/device-plugin-limits.md#背景原理) |
| 裝置準備 | device preparation | kubelet 呼叫 driver 的 NodePrepareResources，取得 container 可用的裝置與 CDI IDs。 | [1.2 背景原理](../01-why-dra/dra-model.md#背景原理) |
| 撤銷裝置準備 | device unprepare | kubelet 呼叫 NodeUnprepareResources 清理節點端狀態，與刪除 Claim 是不同操作。 | [1.2 解除配置與刪除的時點](../01-why-dra/dra-model.md#解除配置與刪除的時點) |
| DeviceClass | DeviceClass | 叢集層級的裝置條件與 driver 設定，供 Claim 引用。 | [1.2 背景原理](../01-why-dra/dra-model.md#背景原理) |
| driver 設定 | driver configuration | 由 driver 解讀的參數，透過 opaque 設定傳遞，scheduler 不用其內容判斷裝置是否符合條件。 | [1.2 背景原理](../01-why-dra/dra-model.md#背景原理) |
| driver 根目錄 | driver root | 提供 driver 執行檔與 library 的主機路徑；本次由 driver container 提供 /run/nvidia/driver。 | [2.2 各元件的責任](../02-deploy/gpu-operator.md#各元件的責任) |
| 工作占空比 | duty cycle | 計算時間占計算與等待總時間的比例；縮短版保留固定 GEMM 與每次等待的比例。 | [第 5 章 固定工作量與校準](../05-performance/README.md#固定工作量與校準) |
| DRA | Dynamic Resource Allocation (DRA) | 以 Class、Slice、Claim 與 driver 協定描述裝置需求、選取與準備的 Kubernetes 資源模型。 | [第 1 章 本章要回答的問題](../01-why-dra/README.md#本章要回答的問題) |
| extended resource | extended resource | 供應者公布、以整數請求的 Kubernetes 額外資源；名稱本身沒有指定 GPU 配額或裝置身分。 | [1.1 整數資源代表什麼](../01-why-dra/device-plugin-limits.md#整數資源代表什麼) |
| 致命 GPU 故障 | fatal GPU fault | 使 MPS server 進入 FAULT 並影響共用故障 GPU 的 client 的錯誤，不等同於單次配置 OOM。 | [4.2 配置失敗與致命 GPU 故障的差別](../04-isolation/mps.md#配置失敗與致命-gpu-故障的差別) |
| 可行配置 | feasible allocation | 同時符合裝置選擇器、供給與 Pod 排程條件的一組裝置配置。 | [6.1 背景原理](../06-heterogeneous/selection.md#背景原理) |
| 功能開關 | feature gate | 控制特定元件功能啟用的開關，Kubernetes 與裝置 driver 有各自的 gate 名稱與成熟度。 | [1.2 Kubernetes 功能成熟度](../01-why-dra/dra-model.md#kubernetes-功能成熟度) |
| 產生的 Claim | generated claim | ResourceClaim controller 依 Pod 引用的 ResourceClaimTemplate 建立並管理的 Claim。 | [2.4 觀察 claim 生命週期](../02-deploy/verify.md#觀察-claim-生命週期) |
| GFD | GPU Feature Discovery (GFD) | 取得 NVIDIA GPU 型號等特徵並公布節點 label 的工具。 | [1.1 屬性如何間接進入排程](../01-why-dra/device-plugin-limits.md#屬性如何間接進入排程) |
| 硬體 work queue | hardware work queue | GPU 中保存依序命令的硬體資源，CUDA driver 將 stream 對應到這些 queue。 | [4.2 Hyper-Q 為何能填補空檔](../04-isolation/mps.md#hyper-q-為何能填補空檔) |
| Hyper-Q | Hyper-Q | 讓多條 GPU work queue 有機會並行排程的硬體機制。 | [4.2 Hyper-Q 為何能填補空檔](../04-isolation/mps.md#hyper-q-為何能填補空檔) |
| hypervisor | hypervisor | 管理虛擬機與硬體存取的虛擬化層。 | [1.3 背景原理](../01-why-dra/gpu-sharing-landscape.md#背景原理) |
| initramfs | initramfs | 開機早期載入的檔案系統，包含啟動所需工具、模組與相關設定。 | [2.1 停用 nouveau](../02-deploy/node-prep.md#停用-nouveau) |
| 攔截 library | interception library | 載入應用 process 並包裝特定 API 呼叫，在轉交真正 driver 前後執行配額或記帳邏輯的 library。 | [4.3 背景原理](../04-isolation/hami-dra.md#背景原理) |
| 中介表示 | intermediate representation | KFP 編譯後以 YAML 保存的 Pipeline 結構與執行設定，供後端建立 Task。 | [6.2 背景原理](../06-heterogeneous/kubeflow.md#背景原理) |
| kernel ABI | kernel ABI | 核心與模組之間的介面版本；同一核心系列的不同 ABI 仍可能需要不同 headers。 | [2.1 核對 headers 是否仍可下載](../02-deploy/node-prep.md#核對-headers-是否仍可下載) |
| kernel headers | kernel headers | 編譯外部 kernel module 時使用的宣告與建置資訊，版本及 flavor 必須匹配正在使用的 kernel。 | [2.1 核對 headers 是否仍可下載](../02-deploy/node-prep.md#核對-headers-是否仍可下載) |
| kernel meta 套件 | kernel meta package | 以套件相依關係追蹤後續 kernel 更新的套件，例如 linux-virtual。 | [2.1 只升級原有 kernel meta 套件](../02-deploy/node-prep.md#只升級原有-kernel-meta-套件) |
| makespan | makespan | 從共同 release 到最後 Task END 的時間；逐個執行基準則加總各 Task 的服務時間。 | [第 5 章 指標的計時邊界](../05-performance/README.md#指標的計時邊界) |
| MPS client | MPS client | 透過 CUDA driver 連入 MPS server 的 CUDA 執行環境；記憶體配額作用在 client process。 | [4.2 背景原理](../04-isolation/mps.md#背景原理) |
| MPS control daemon | MPS control daemon | 啟動與停止 MPS server，並協調 CUDA client 與 server 連線的 process。 | [4.2 背景原理](../04-isolation/mps.md#背景原理) |
| MIG | Multi-Instance GPU (MIG) | 在支援的 GPU 上建立具有分開運算與記憶體資源的 GPU Instance。 | [1.3 共用方法比較](../01-why-dra/gpu-sharing-landscape.md#共用方法比較) |
| MPS | Multi-Process Service (MPS) | 讓多個 CUDA client process 共用 GPU 執行資源的 NVIDIA 服務；記憶體配額的 process 範圍須另行核對。 | [1.2 Driver 專屬設定](../01-why-dra/dra-model.md#driver-專屬設定) |
| NFD | Node Feature Discovery (NFD) | 探索節點硬體等特徵並建立供排程使用之節點 label 的元件。 | [1.1 屬性如何間接進入排程](../01-why-dra/device-plugin-limits.md#屬性如何間接進入排程) |
| NVML | NVIDIA Management Library (NVML) | 查詢與管理 GPU 狀態的介面，也是 nvidia-smi 的底層 library。 | [1.2 實際的裝置清單](../01-why-dra/dra-model.md#實際的裝置清單) |
| 觀測上界 | observed upper bound | 事件已被觀測時的時間上限，例如 watch 收件時刻；不等於事件在 controller 內實際發生的時刻。 | [6.3 H3 與 H4 的等待與再配置](../06-heterogeneous/experiments.md#h3-與-h4-的等待與再配置) |
| OOMKilled | OOMKilled | container 被系統記憶體 OOM 機制終止時的 Kubernetes 終止原因。 | [第 3 章 背景原理](../03-oom/README.md#背景原理) |
| ownerReference | ownerReference | 指出 API 物件的擁有者，範本產生的 Claim 以 Pod 作為擁有者供生命週期管理。 | [1.2 解除配置與刪除的時點](../01-why-dra/dra-model.md#解除配置與刪除的時點) |
| p95 | p95 | 排序後位於第 95 百分位的位置；本書 OOM 實驗使用相鄰樣本線性插值計算。 | [第 3 章 指標如何計算](../03-oom/README.md#指標如何計算) |
| 配對加速比 | paired speedup | 以同一個 block、工作與 Task 數的基準時間除以被測時間。 | [第 5 章 指標的計時邊界](../05-performance/README.md#指標的計時邊界) |
| PCI function | PCI function | PCI 裝置可各自辨識的功能；本次 RTX 4090 的 GPU 與音訊功能一起直通。 | [2.1 確認硬體可見](../02-deploy/node-prep.md#確認硬體可見) |
| 相位錯開 | phase staggering | 為同批 Task 設定不同的初始等待；不保證後續 burst 的相位保持固定。 | [第 5 章 初始錯開能讓 time-slicing 填補空檔](../05-performance/README.md#初始錯開能讓-time-slicing-填補空檔) |
| 實體上限 | physical upper bound | Pod 所有 GPU process 的實體 VRAM 合計不超過請求量的要求。 | [第 4 章 三層隔離模型](../04-isolation/README.md#三層隔離模型) |
| 壓力窗口 | pressure window | 壓力 process 仍保留記憶體時的觀察區間，排除釋放後的配置成功。 | [第 3 章 背景原理](../03-oom/README.md#背景原理) |
| 候選優先清單 | prioritized list / firstAvailable | 亦稱優先替代清單；依序列出同一個 request 可接受的子請求，選擇可滿足者並將順序納入排程偏好。 | [1.2 需求可以如何延伸](../01-why-dra/dra-model.md#需求可以如何延伸) |
| process 槽位 | process slot | 共享區域中保存 process 識別與 context、module、data 等記帳欄位的一筆紀錄。 | [4.3 記憶體配置如何計量](../04-isolation/hami-dra.md#記憶體配置如何計量) |
| 就緒檢查 | readiness check | 以 Pod Ready 條件判斷持續服務是否可用；一次性工作成功終止另以 Succeeded 判斷。 | [2.2 讀取 Pod 狀態](../02-deploy/gpu-operator.md#讀取-pod-狀態) |
| 開始延遲 | release lateness | Task 實際開始時間相對共同 release 的延遲。 | [第 5 章 配對矩陣與同步開始](../05-performance/README.md#配對矩陣與同步開始) |
| 共用名額 | replica (Device Plugin time-slicing) | plugin 將實體 GPU 重複公布後形成的可排程資源單位，不是新增的實體 GPU。 | [1.1 部署與設定](../01-why-dra/device-plugin-limits.md#部署與設定) |
| reservedFor | reservedFor | ResourceClaim 狀態中允許消費者使用既有配置的參照清單；與裝置配置結果不同。 | [1.2 物件如何連接](../01-why-dra/dra-model.md#物件如何連接) |
| resident tensor | resident tensor | 持續保留並用來執行既有運算的 GPU tensor。 | [第 3 章 背景原理](../03-oom/README.md#背景原理) |
| 資源池 | resource pool | 同一 driver 管理的一組裝置清單，可由多個同世代的 ResourceSlice 組成。 | [1.2 背景原理](../01-why-dra/dra-model.md#背景原理) |
| ResourceClaim | ResourceClaim | 保存裝置需求、driver 設定、配置結果與保留使用者的 Namespace 物件。 | [1.2 背景原理](../01-why-dra/dra-model.md#背景原理) |
| ResourceClaimTemplate | ResourceClaimTemplate | 一般 Pod 引用後由 controller 建立屬於該 Pod 的 Claim 的範本。 | [1.2 背景原理](../01-why-dra/dra-model.md#背景原理) |
| ResourceSlice | ResourceSlice | driver 公布的裝置、屬性、容量及資源池資訊。 | [1.2 背景原理](../01-why-dra/dra-model.md#背景原理) |
| runtime 配額 | runtime allocation quota | 在指定 client 或共享範圍內，CUDA 配置超過配額時回報失敗的限制。 | [第 4 章 三層隔離模型](../04-isolation/README.md#三層隔離模型) |
| 排程記帳 | scheduling-time accounting | 依 Claim 已配置的容量消耗，判斷新需求是否仍符合公布總量；不直接攔截 CUDA 記憶體配置。 | [1.2 物件如何連接](../01-why-dra/dra-model.md#物件如何連接) |
| 共享 cache | shared cache (HAMi-core) | 保存被攔截配置路徑用量的共享紀錄，須實測其跨 process 與跨 Pod 的合計範圍。 | [4.3 記憶體配置如何計量](../04-isolation/hami-dra.md#記憶體配置如何計量) |
| 共同保留窗口 | shared hold interval | 多個 B client 的 tensor 保留區間交集，用來計算壓力穩定期間 A 的 probe。 | [第 4 章 CUDA runtime 測試](../04-isolation/README.md#cuda-runtime-測試) |
| 共享區域 | shared region | 多個 process 透過同一 cache 檔案的 MAP_SHARED 映射存取的記憶體區域，HAMi-core 用來保存限制與 process 用量。 | [4.3 記憶體配置如何計量](../04-isolation/hami-dra.md#記憶體配置如何計量) |
| 快照 barrier | snapshot barrier | workload 等待本機標記，讓外部腳本先保存仍配置中的 Claim 狀態。 | [2.4 檢查程式](../02-deploy/verify.md#檢查程式) |
| start skew | start skew | 同批 Task 中最晚與最早的實際開始時間差。 | [第 5 章 配對矩陣與同步開始](../05-performance/README.md#配對矩陣與同步開始) |
| SM | Streaming Multiprocessor (SM) | GPU 執行工作的運算單元；MPS active thread 上限不等於保留專用 SM。 | [1.2 Driver 專屬設定](../01-why-dra/dra-model.md#driver-專屬設定) |
| Task 吞吐量 | task throughput | 單位時間完成的固定工作量 Task 數，第五章使用 Task/s。 | [第 5 章 指標的計時邊界](../05-performance/README.md#指標的計時邊界) |
| time-slicing | time-slicing | 讓共用 GPU 的 CUDA 工作輪流執行；DRA 分享策略名稱為 TimeSlicing，time-slicing 本身不提供 VRAM 配額隔離。 | [1.1 部署與設定](../01-why-dra/device-plugin-limits.md#部署與設定) |
| 時間加權平均 | time-weighted mean | 按每個取樣值涵蓋的量測窗口時間加權，避免不規則取樣間隔造成偏差。 | [第 5 章 配對矩陣與同步開始](../05-performance/README.md#配對矩陣與同步開始) |
| token throttling | token throttling | 依使用率調整可提交工作的額度，額度不足時延後 kernel launch 的軟體限制。 | [4.3 SM 比例如何 throttle](../04-isolation/hami-dra.md#sm-比例如何-throttle) |
| utilization limiter | utilization limiter | 依使用率回饋與額度延後 kernel 提交的軟體機制，不是固定分割 SM。 | [第 5 章 HAMi-DRA 在普通 context 路徑前限制提交速度](../05-performance/README.md#hami-dra-在普通-context-路徑前限制提交速度) |
| GPU 記憶體 | video RAM (VRAM) | GPU 裝置上的記憶體；總容量、Claim 申請量、tensor 保留量與實體使用量須分別閱讀。 | [1.1 整數資源代表什麼](../01-why-dra/device-plugin-limits.md#整數資源代表什麼) |
| vGPU | virtual GPU (vGPU) | 由 vGPU Manager 與 hypervisor 提供給 VM 的 GPU 資源，可使用不同後端機制。 | [1.3 共用方法比較](../01-why-dra/gpu-sharing-landscape.md#共用方法比較) |

## 重點整理

查閱配額或選卡結果時，請保留名詞的作用範圍：Claim 配置不等於裝置準備，帳本餘額不等於實體空閒，CUDA 配置失敗也不等於 container OOMKilled。相關數值與版本可接著查閱[版本與 image](versions.md)及[重現對照表](reproduction-map.md)。

<!-- nav:start -->
[← 上一頁：第 7 章　結論與實務建議](../07-conclusion.md) ｜ [目錄](../README.md) ｜ [下一頁：附錄 B　版本與 container image →](versions.md)
<!-- nav:end -->
