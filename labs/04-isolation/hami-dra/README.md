# HAMi-DRA 配額與作用範圍復現套件

本套件在同一顆 A40 上重現 HAMi-DRA 的容量帳本、單行程與雙行程配額邊界，以及兩個 CUDA client 共用 Claim 的三種拓撲。機制、完整 YAML 與原始結果見[第四章 HAMi-DRA](../../../book/04-isolation/hami-dra.md)，縮短版在原始叢集的執行結果與還原紀錄見[實機驗證紀錄](../VERIFIED.md)。所有批次工作使用 `backoffLimit: 0`、`restartPolicy: Never` 的 Job，避免重試掩蓋第一次 CUDA OOM 或控制停滯。

## 前置條件

從本儲存庫的完整 clone 執行，保留相鄰的 `common/`、`provider/` 及 `labs/common/`。分析原始精選資料時也需要 `data/e2/` 與 `data/e2b/`。控制端需要 Bash、Python 3 與含 Kustomize 的 `kubectl`；本機控制與分析只使用 Python 標準函式庫，PyTorch 和 CUDA 在容器中執行。

先完成[第二章的部署](../../../book/02-deploy/README.md)。本套件固定使用 `dra-a40` 與 `GPU-29ffee3e-bee4-e341-917a-fe7e43e977af`，原始環境是 Kubernetes v1.37.0。移植到其他 A40 時須一致修改 provider affinity、Claim CEL、控制端固定值及取樣目標，再重新驗證；套件不會自動選擇其他 GPU。

- A40 必須沒有 CUDA compute 行程、沒有既有裝置 Claim、沒有 A40 MPS daemon，compute mode 為 `Default`，VRAM 使用量不超過 64 MiB。這是原實驗的進入條件。
- 原生 NVIDIA kubelet plugin DaemonSet 必須仍是 `nvidia-dra-driver-gpu/dra-driver-nvidia-gpu-kubelet-plugin`。安裝腳本沿用其 ServiceAccount `dra-driver-nvidia-gpu-service-account-kubeletplugin`，核對原生 RBAC，再建立本套件獨立且帶標籤的 ClusterRole／ClusterRoleBinding；不改寫原生 ServiceAccount 或 RBAC。
- GPU Operator 在目標節點的 driver Pod 與 `nvidia-driver-ctr` 必須 Ready。取樣透過 `kubectl -n gpu-operator exec` 執行 `nvidia-smi --query-gpu=... --format=csv -lms 500`，不需要 SSH。
- 操作者需要 `gpu-isolation-lab` 的 Job、Pod、ConfigMap、ResourceClaim、events、logs、exec 與刪除權限，以及全叢集 ResourceClaim、ResourceSlice、DeviceClass 的查詢權限。清理 Namespace 另需列出其中各種資源的權限，確認只剩 Kubernetes 預設物件及 events 後才刪除。provider 操作另需 DaemonSet、ClusterRole／Binding 的讀寫權限及建立 privileged node debug Pod 的權限。
- 主機 `/usr/local/vgpu`、`/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io`、`/tmp/vgpulock` 必須不存在。任何既有路徑都會讓安裝停止，保留其原內容；不能用本套件接管已安裝的 HAMi。

| 固定項目 | 設定 | 目的 |
|---|---|---|
| HAMi provider | `docker.io/projecthami/k8s-dra-driver:v0.1.0@sha256:ae7e844acbd099a109bfa84296905ea941eed292a7991c06e2eeee443457ede3` | 固定原 provider 映像檔 |
| 工作負載與 host helper | `docker.io/pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime@sha256:c8268a92a69bd500f8be0e665b2630ee006dadaf7bfbc24249141b15ff622755` | 沿用原 CUDA／PyTorch；helper 不建立 CUDA context |
| `libvgpu.so` SHA256 | `56fc0944896337cc0ee439cbc7364105ccac11e8dc38add6a3dc467cea34b18f` | 拒絕把不同函式庫混入配額比較 |
| DeviceClass | `hami-core-gpu.project-hami.io` | 同時符合第五章的預設 `HAMI_DEVICE_CLASS` |
| feature gates | `HAMiCoreSupport=true,MPSSupport=false,TimeSlicingSettings=false,DynamicMIG=false,ComputeDomainCliques=false,PassthroughSupport=false` | 沿用原 HAMi provider 的分享方式 |

## 安裝與還原 provider

**這兩個獨立腳本會改變 A40 的 DRA provider、建立叢集層級物件，並寫入或刪除節點主機檔案。只在專用實驗叢集及 GPU 閒置時執行，保留備份直到還原驗證完成。** `run.sh` 只檢查現有 provider，不會切換 provider。先使用專屬的 provider-run-id；它與工作負載 run-id 分開。

```sh
$ cd labs/04-isolation/hami-dra
$ export I_UNDERSTAND_THIS_CHANGES_THE_CLUSTER=1
$ bash scripts/install-provider.sh hami-provider
```

provider YAML 位於 [manifests/provider/](manifests/provider/)。腳本先記錄主機路徑，再呼叫共用的 `../provider/backup.sh` 與 `exclude-node.sh dra-a40`：備份原 DaemonSet UID／spec、使用 `OnDelete` 排除 A40、帶 UID／resourceVersion 前置條件刪除原 plugin Pod 及過期 ResourceSlice，並持續核對 `dra-4090` 的 plugin UID。之後建立只會排到 A40 的 `a40-hami-provider`，確認唯一 A40 publisher、DeviceClass selector 及已安裝的函式庫摘要。

DaemonSet 保留原 provider 的五個 affinity OR 條件、env、啟動命令、探針、初始化容器與 host mounts，並在每個 affinity 條件加入 A40 限制。映像檔欄位以 digest 固定；`IMAGE_NAME` 保留原 E2 的 `docker.io/projecthami/k8s-dra-driver:v0.1.0` tag。原 E2B 記錄中的 `IMAGE_NAME` 已改用 digest，因此本套件選的是 E2 的 env 值，而不是宣稱兩次輸入完全相同。

安裝中斷可用相同 provider-run-id 重入。腳本只接受相同 instance、spec 與已記錄 UID 的物件；成功安裝後保留第一次完成的 host snapshot。失敗時另保存 `provider-failed.json` 與 `host-install-failed.json`。除下表列出的固定執行期檔案外，快照之外的未知檔案會阻止主機清理；初始化未完成時，先以相同 ID 完成安裝，建立穩定快照。

工作負載全部收集、清理後，用同一個 provider-run-id 解除安裝：

```sh
$ bash scripts/uninstall-provider.sh hami-provider
```

解除安裝先核對沒有 A40 Claim、GPU 閒置、本套件 provider UID／spec 與原生備份狀態，再刪除本次標籤及 instance 的 HAMi DaemonSet，等待 Pod 消失，沿用 `remove-provider-slices.sh` 清除 HAMi A40 ResourceSlice。接著立即呼叫 `restore.sh`，等待原生 plugin 與 ResourceSlice 回來，逐欄核對原 DaemonSet UID／spec 與 4090 plugin UID。完成還原驗證後，才刪除本次 DeviceClass、附加 RBAC，並以固定映像檔的 `kubectl debug node/dra-a40 --profile=sysadmin` helper 清理下表的主機檔案。

主機清理失敗仍會讓解除安裝回傳非零狀態，並明確印出原生 provider 已完成還原驗證。先查看 helper 錯誤、`host-after-provider-stop.json` 與 `provider-backup/restored.json`；helper 的執行錯誤會保留原 exit code，空白或無效回應也會直接回報，不再接著解析失敗輸出。處理阻止清理的殘留物後，用相同 provider-run-id 重試；腳本重新驗證原生 UID／spec 與主機狀態，既有 `host-cleanup.verified` 也不能取代最後的主機路徑查核。

| 主機路徑 | 用途 | 清理條件 |
|---|---|---|
| `/usr/local/vgpu` | `libvgpu.so`、`ld.so.preload` 與 Claim cache | 安裝前不存在；檔案 identity 與摘要符合安裝快照；Claim 子目錄已由 Unprepare 移除 |
| `/var/lib/kubelet/plugins/hami-core-gpu.project-hami.io` | plugin socket、checkpoint 與鎖定檔案 | 安裝前不存在；快照後新增的檔案只接受此目錄直接包含的 `pu.lock`、`cp.lock`、`checkpoint.json`，須為 root 擁有且與原目錄同一裝置的普通檔案；checkpoint 的 `v2.preparedClaims` 為空 |
| `/tmp/vgpulock` | HAMi 鎖定目錄 | 安裝前不存在；identity 符合快照 |
| `/var/run/cdi` | CDI edits | 只查核；不得殘留 HAMi CDI 檔案，不刪除其他 provider 的 CDI |
| `/var/tmp/dra-hami-<run-id>-<case>-r<repeat>` | scope 的兩個 client 控制目錄 | owner、dev／inode／UID 符合本輪紀錄，兩個 CUDA 容器均已退出，內容只含已知控制檔案 |

主機清理只使用逐檔 `unlink` 與空目錄 `rmdir`；未知檔案、symlink、identity 變動或未釋放的 checkpoint 都會停止。Claim cache 的路徑為 `/usr/local/vgpu/claims/<claimUID>/<cache-uuid>.cache`，cache 檔名由 provider 另產生 UUID，不能把它當作 DRA allocation 的 `shareID`。判斷是否共用快取使用實際 CUDA 行程的 memory maps 及檔案 dev／inode。

## 執行縮短版

```sh
$ SMOKE=1 bash scripts/run.sh hami-smoke
```

預設 `MODE=runtime`，縮短版依序跑 single、dual-process 及 scope case4a，各一輪。它保留原 CUDA 工作、256 MiB 步進、runtime 的 60 秒 baseline／60 秒 recovery 及 30 秒保留時間；縮短的是重複輪數與 scope 拓撲數。它適合驗證 provider 接線、實際 PID 對應、配額事件、收集與清理，不能取代完整三輪的原始數字。

也可單獨檢查一種流程：

```sh
$ MODE=accounting SMOKE=1 bash scripts/run.sh hami-accounting-smoke
$ MODE=scope SMOKE=1 bash scripts/run.sh hami-scope-smoke
```

`MODE=scope SMOKE=1` 只跑 case4a 一輪；`MODE=accounting SMOKE=1` 只跑容量帳本一輪。每次新執行使用新的 run-id。同一 ID 已完成時只重算分析；開始後中斷的 ID 必須先收集、清理，再使用新 ID。

## 執行完整模式

```sh
$ MODE=accounting bash scripts/run.sh hami-accounting-full
$ MODE=runtime bash scripts/run.sh hami-runtime-full
$ MODE=scope bash scripts/run.sh hami-scope-full
```

三個 MODE 使用不同 run-id，依序執行即可重現全部條件。accounting 跑三輪；runtime 的 single／dual-process 各三輪；scope 的 case3／case4a／case4b 各三輪。所有 Job 結束並清理後才開始下一輪，Namespace `gpu-isolation-lab` 必須沒有其他復現工作。

| 條件 | Claim 與 CUDA client | 原工作參數 |
|---|---|---|
| accounting | resident `16Gi`、release `24Gi`、waiter `8Gi`，各 cores `10` | 小 CPU／RAM 的 `nvidia-smi -L; sleep 3600`；第三個 Job 因容量不足 Pending，釋放 `24Gi` 後才可排程 |
| runtime single | steady-service 的 `4Gi`／cores `50` 與 quota-pressure 的 `8Gi`／cores `50` 分開 | A 持續探測；B 每次增長 256 MiB，CUDA OOM 或成功越界後保留 30 秒 |
| runtime dual-process | B 的同一容器內有兩個 CUDA 行程，共用 `8Gi` Claim | 每個 client 目標 `5Gi`；只在成功配置後觀察合計是否超過 `8Gi`，不事先拒絕配置 |
| scope case3 | 一個 Pod 的兩個容器共用一個 `8Gi`／cores `50` Claim | 每個 client 目標 `6Gi`，每輪共同 allocate／snapshot barrier，最多 24 次步進 |
| scope case4a | 兩個 Pod 共用一個 `8Gi`／cores `50` Claim | 同上；先讓第一個 CUDA client Ready，再建立第二個 Job |
| scope case4b | 兩個 Pod 各有獨立 `8Gi`／cores `50` Claim | 同上；每個 client 有獨立 cache inode |

accounting 的兩個已配置 Claim 合計保留 cores `20`，剩餘 `80`；runtime 的 A／B 合計保留 cores `100`，剩餘 `0`。scope 沒有以合計 `8Gi` 設定人工停止線：每個行程仍嘗試到 `6Gi` 或真正的 CUDA OOM，避免預先封住要觀察的 case4a 越界。

## 部署檔案與原始名稱對照

[workload/hami_scope_workload.py](workload/hami_scope_workload.py) 沿用原 E2B CUDA 工作；runtime 使用相鄰共用套件中的原 E2 工作。ConfigMap 由 Kustomize generator 產生，沒有複製龐大的 Python 內容到 YAML。可以離線檢視完整部署檔：

```sh
$ kubectl kustomize manifests/runtime/single --load-restrictor=LoadRestrictionsNone
$ kubectl kustomize manifests/runtime/dual-process --load-restrictor=LoadRestrictionsNone
$ kubectl kustomize manifests/scope/case4a --load-restrictor=LoadRestrictionsNone
```

scope YAML 的 `__RUN_ID__`／`__REPEAT__` 由 runner 在本機渲染成確切的控制路徑及 instance 標籤，因此應使用 `run.sh` 建立，不直接 `apply -k`。所有本套件物件有 `app.kubernetes.io/part-of: dra-book` 與角色 `component`；runner 另加 run-id 的 `instance`，並保存實際 Job Pod UID 及容器 ID。

| 原始物件 | 本套件物件 |
|---|---|
| `e2-hami-provider`／`e2b-hami-provider` Pod | `a40-hami-provider` DaemonSet |
| `e2-hami-gpu-20261007`／`e2b-20261007t060500z-gpu` DeviceClass | `hami-core-gpu.project-hami.io` |
| accounting Pod／Claim `a`、`b`、`c` | Job `capacity-resident`、`capacity-release`、`capacity-waiter`；各自同角色的 `-gpu` Claim |
| runtime Pod `survivor`、`pressure`；Claim `survivor-gpu`、`pressure-gpu` | Job `steady-service`、`quota-pressure`；Claim `steady-gpu`、`pressure-gpu` |
| case3 Pod `both`、Claim `quota-0` | Job `container-scope-pressure`、Claim `container-scope-gpu` |
| case4a Pod `pod-0`／`pod-1`、Claim `quota-0` | Job `scope-primary`／`scope-secondary`、Claim `pod-shared-gpu` |
| case4b Pod `pod-0`／`pod-1`、Claim `quota-0`／`quota-1` | Job `scope-primary`／`scope-secondary`、Claim `worker-primary-gpu`／`worker-secondary-gpu` |

## 收集與分析

輸出預設位於共用的 `../out/<run-id>/`，provider 備份與各 MODE 結果各用自己的 ID。設定 `OUT_DIR` 可改位置；之後 collect、cleanup、uninstall 必須使用同一 `OUT_DIR`，不能指向另一份備份。

```text
../out/hami-provider/
  provider-backup/original-daemonset.json, original-daemonset.yaml, backup.json
  provider-input.yaml, provider-expected.json, provider-owned.json
  host-before.json, host-installed.json, host-restored-clean.json
  provider-installed.verified, host-cleanup.verified
../out/<workload-run-id>/
  run.json, target.json, commands.log, completed.json
  <condition>-r<repeat>/
    trial.json, manifests.yaml, manifests.json, claims-allocated.json
    task-a.log, task-b.log, runtime-hooks.json, client-identity.json
    client-processes.json, processes-during.txt, gpu.csv, phases.json
    ready.json, identity.json, round-XX.json, final.json
    client-0.log, client-1.log, hold-samples.jsonl, terminations.json
    control-owner.json, scope-command.json, scope-previous-command.json
    control-final.json, control-removed.json
    scope-launches.json, scope-claims-created.json, scope-objects-current.json
    scope-cleanup-started.json, scope-cleanup-objects.json
    host-after-unprepare.json, cleanup.json, cleaned.json
  summary.json, summary.csv, analysis-collect.log
```

每種 MODE 只產生其流程使用的檔案。runtime 保存 A 的 probe 日誌、B 配置事件、真正 CUDA 行程與宿主機 PID／容器 ID 對應；scope 保存兩個 client 的 READY、每個完成的雙方 barrier、外部 NVML 與實際容器終止。缺少 barrier、釋放事件、實際退出或清理證據時，分析輸出保留 `incomplete`；最後一個完成 barrier 不能冒充 final boundary。runner 在收集期間把暫時的判定保存在 `analysis-collect.log`，完成所有清理驗證後才在終端機為每輪印出一次最終 verdict。

```sh
$ bash scripts/collect.sh hami-smoke
$ python3 analysis/analyze.py ../out/hami-smoke --output ../out/hami-smoke
```

原始精選資料也可完全離線重算：

```sh
$ python3 analysis/analyze.py ../../../data/e2/hami/raw --output ../out/hami-e2-golden
$ python3 analysis/analyze.py ../../../data/e2b/raw --output ../out/hami-e2b-golden
```

原始結果的判讀與來源見 [E2 資料](../../../data/e2/hami/SOURCES.md)及 [E2B 資料](../../../data/e2b/SOURCES.md)。單行程 B 在 `7.5GiB` 後 CUDA OOM；雙行程共用 cache 時成功 tensor 合計為 `7.25–7.5GiB`，外部 NVML 最高為 `8292MiB`，高於 `8192MiB` Claim。case3 合計 `7.25GiB`；case4a 兩輪到 `9.75GiB`，第三輪停滯而沒有 final boundary；case4b 合計 `12GiB`。新叢集應先確認資料完整及拓撲相符，不把這些歷史邊界值當作必須通過的斷言。

## 清理與中斷處理

先收集，再清理；最後才解除 provider：

```sh
$ bash scripts/collect.sh hami-smoke
$ bash scripts/cleanup.sh hami-smoke
$ bash scripts/uninstall-provider.sh hami-provider
```

cleanup 核對 Namespace、物件標籤、instance 與保存的 Pod UID，只刪除本次 `dra-book` 工作。runtime 先傳送 STOP 等待行程真正退出；scope 先要求兩個 client 同步 RELEASED，確認所有 CUDA 容器已終止及外部 compute 行程消失，再刪除 Job／Claim，等待 cache、CDI、checkpoint 不再含 Claim UID，最後核對控制目錄 owner 後刪除。

所有 GPU 與主機清理驗證完成後，cleanup 會列出 Namespace 中各種資源，除 `default` ServiceAccount、`kube-root-ca.crt` ConfigMap 及 events 外，任何剩餘物件都會讓清理停止並保留 Namespace。確認清空後再核對 Namespace UID 與 `dra-book` 標籤，帶 UID／resourceVersion 前置條件刪除 `gpu-isolation-lab`，並等待它真正消失；同名物件被替換或標籤改變會停止，Namespace 已不存在時則直接結束。

scope 每次發送前保存最新與前一個命令。若發送在兩個 client 之間中斷，重入會先以 owner 與完整命令內容核對，冪等重播同一個序號，等待 ACK barrier 後才產生下一個 STOP；已發送的 STOP 不再增加序號。若兩個實際 CUDA 容器都已終止，cleanup 直接保存終止證據，不再發送控制命令；這允許控制期限結束後清理，但不會把缺少 RELEASED 的失敗量測判定為成功。

case4a／case4b 先啟動 primary，再啟動 secondary。每次建立 Job 前先保存建立意圖，建立後記錄 API 回傳的 UID／spec；只有沒有建立意圖、沒有保存過 Pod，且最新 API 查詢也沒有該 Job／Pod 時，cleanup 才能排除尚未啟動的 secondary。已嘗試建立但證據不全、曾保存的物件消失，或 UID／spec 被替換時，都會停止清理。

刪除前，cleanup 另保存實際終止狀態與本輪物件清單作為清理階段紀錄；Claim UID 在 Job 啟動前即已保存，因此 primary 啟動途中失敗仍能核對 cache、CDI 與 checkpoint。若刪除 Job／Claim 或移除控制目錄途中中斷，下次 cleanup 使用這份紀錄續做，核對任何仍存在物件的 UID／spec 及控制目錄 dev／inode／owner；新物件、替換物件與未知檔案都會阻止續做。

控制逾時時保留已有日誌與物件，不會把 CUDA OOM、CPU／RAM `OOMKilled`、未啟動容器或沒有完整雙方 barrier 的停滯混成成功邊界。若容器無法正常退出，cleanup 會停止並保留控制目錄與 Claim；先查 `run-error.txt`、events、終止狀態及 host snapshot。原生 RBAC、provider 備份與本機證據保留供查核；解除安裝每次都重新確認 GPU 與 host 狀態。

## 第五章共用 provider

[第五章效能套件](../../05-performance/README.md)可直接呼叫這兩支 provider 腳本，預設 `HAMI_DEVICE_CLASS=hami-core-gpu.project-hami.io` 不需覆寫。先由第五章的原生設定腳本準備 NVIDIA 模式，再用本套件的安裝腳本備份**當下已準備的原生狀態**；在每個 HAMi 邊界使用新的 provider-run-id，解除安裝會回到該原生狀態。全部矩陣結束後，才由第五章的還原腳本回復第二章最初的設定；不要先刪掉本套件的 `provider-backup/`。
