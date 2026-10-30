# Windows ANTs Atlas pilot

## 2026-10-29：SyN 与病例级并行工程实现（未放行）

本节对应 `docs/SyN_parallel_execution_design_20261029.md`，覆盖下方历史“未实现SyN”和“仅串行”说明；历史仿射静态PASS不覆盖本次改动。实施起点为 `main@2907411641a84dda24ded2a089d71aab1f6732d9`；保持现有main顺序开发，未提交、未推送、未连接服务器。保留开始时已存在的未跟踪docs目录。不修改segementation、项目记忆、原始医学数据、固定split或旧run。

本轮按用户要求以静态审查为主，仅新增5项小型计划/调度回归，保留旧20项测试；不增加大型几何测试框架。不创建worker/reviewer/聊天，由当前唯一实施者直接改生产代码。自审不算独立Level3。真实ANTs非交换形变、逆场域、Jacobian和解剖质量仍须部署验证，不把命令检查或mock调度当作几何通过。

### 来源身份与方法边界

- 固定官方来源：<https://raw.githubusercontent.com/ANTsX/ANTs/v2.6.5/Scripts/antsMultivariateTemplateConstruction2.sh>，raw SHA256为 `80441088eda2195b98286dc04db71d2878e41e3ea1f2d8f0db26d84bb6639d87`。实施前独立下载到临时参考副本并核对字节；本机原固定副本实际在 `CG-SAMR-reference/docs/reference_sources/ANTs_v2.6.5/antsMultivariateTemplateConstruction2.sh`，哈希与下载一致。`ANTs/Scripts/`内的另一副本不是旧固定来源；旧`SOURCE_SHA`原样保留。新方法独立使用`syn_official_script`和`SYN_SOURCE_SHA`，虽字节相同，身份不借用可漂移的源码树路径。
- 论文：<https://pmc.ncbi.nlm.nih.gov/articles/PMC8543492/>，III.A支持训练集群体Atlas与multivariate template construction；未公开具体SyN命令/轮数。本实现不是作者参数复现。
- 项目候选：两轮、`SyN[0.1,3,0]`、CC半径4、`[100x100x70x20,1e-6,10]`、shrink `6x4x2x1`、smooth `3x2x1x0vox`、shape gradient `0.25`。固定脚本接受显式SyN参数，但其默认SyN步长为0.2，故0.1是项目候选。Rigid/Affine沿用已固定配置；种子、Z8/10、N4关闭、锐化关闭、每检查等权不变。`AverageImages mode2`是归一化均值且不锐化，不等同于脚本`-a 2`的median。
- 按固定版本`shapeupdatetotemplate`核对：影像mode2平均；实际前向`*1Warp`字段按mode0平均；乘`-0.25`；`AverageAffineTransform`；用逆平均Affine与`-e physical-vector`变换平均字段；图像链为逆平均Affine后列同一字段四次。四次复合不替换成字段乘4。脚本字段说明中的inverse措辞不作为改用`1InverseWarp`的依据，实际文件选择才是依据。保留包含刚体成分的平均Affine，不静默换成NoRigid。
- 有意适配：Windows Python调度；固定质心/平均初始化；源图每轮重新注册；每步独立文件而不覆盖中间字段；显式seed；额外保存注册影像；关闭N4/锐化；严格输出与向量检查。这些不是论文规定。

### CLI、配置与调度

入口仍为 `atlas.py --mode plan|execute --config ... --evidence ... --split ... --output-root ... --run-id ...`，默认plan；没有自动全量执行按钮。旧配置省略`method`继续使用`rigid_affine`、2线程、串行，旧pilot/full计划仍为194/1814步。新增`method=rigid_affine_syn`和`execution_mode=atlas|benchmark`；`threads`和`parallel_jobs`只接受真正正整数，准备预算限于jobs×threads `1×2`、`4×4`、`4×6`，拒绝bool或任意扩大。

新示例为 `configs/syn_pilot.example.json` 与 `configs/benchmark.example.json`。都含占位私有路径/哈希、`execute_authorized=false`、`geometric_qc_accepted=false`、`syn_deployment_validation_accepted=false`，不可直接执行。新全量配置暂不创建，须先完成小样方法验收。

每例任务组包括注册、正/逆场检查、重采样与输出检查；不同病例并行，同例顺序。初始化、每轮及最终冻结模板阶段之间有屏障，平均输入始终按manifest顺序。每例独立前缀、每条命令独立日志。子进程ITK按任务线程，OMP/BLAS/MKL/NumExpr/vecLib设1，内部SimpleITK图像操作限1线程并在退出时恢复，记录在receipt，不改永久环境；不同线程不承诺位级一致。

`scheduler.py`只通过事件将结果交给协调器；仅协调器写临时run.json并`os.replace`发布。记录步骤ID/病例任务ID、真实开始结束、PID、退出码、输入/输出身份、工具哈希、线程、成功数及`active_tasks`。`completed_steps`不按最大索引推断。失败立即停止派发，已有病例组自然结束后收集结果，不执行依赖平均，最终FAILED；不自动重试/清理/续跑。run-id唯一；规范化路径拒绝原始数据、证据、代码、工具和旧run内部；原始数据只读。

SyN产物要求Affine、Warp、InverseWarp及注册输出全部存在、非空。M→F图像pull链为`-t Warp -t Affine`，F→M为`-t [Affine,1] -t InverseWarp`，不用字段取负制造逆场，不用于点坐标。字段检查3D/3分量、有限值、物理LPS/mm约定与固定网格；Jacobian按`du/dindex @ inverse(direction @ diag(spacing)) + I`计算，包含斜direction，非正值失败。文件本身无法独立证明LPS语义，需部署验证receipt。最终回映以原始源网格为reference，覆盖域来自源采集网格全1且NN，检查二值；覆盖域不是脑组织覆盖。完成计算仍是`COMPUTED_PENDING_GEOMETRIC_QC`。

### SyN执行前必须有的私有几何证据

除显式execute授权外，要求`syn_deployment_validation_accepted=true`及SHA固定的`syn_validation_receipt`，字段必须包含：

- `status=PASS`、`official_source_sha256`、当前`atlas_sha256`与`scheduler_sha256`；`exe_sha256`按计划中绝对可执行文件路径映射到真实哈希，`tool_versions`记录真实部署版本。
- `real_windows_ants`、`noncommuting_chain`、`oblique_lps_spacing`、`identity_field`、`analytic_translation`、`physical_vector_shape_update`、`native_grid`、`binary_coverage`、`jacobian`均为true；必须来自真实Windows部署工具，不接受mock证明。
- `interior_roundtrip_error_min_spacing`为有限非负数且<0.25，`boundary_error_reported=true`，边界误差单列。

这份receipt由后续真实验证形成，不随代码伪造生成。全量SyN还要求独立的`syn_pilot_geometric_qc_accepted`、SHA固定的`syn_pilot_qc_receipt`，其method必须为`rigid_affine_syn`、status为PASS并标识当前atlas代码。旧仿射的QC布尔值不能单独放行SyN。

### 有界测速准备（本轮未启动）

benchmark经同一个prepare读取完整已验收pilot10，然后只选其固定前8例。`benchmark_manifest_sha256`为前8项records的canonical JSON SHA256：`json.dumps(records[:8], sort_keys=True, separators=(',', ':')).encode()`的SHA256，包含原顺序。固定模板的路径、字节SHA、SHA固定的`benchmark_template_receipt`均须提供；receipt记录`template_sha256`、固定`split_sha256`、`gt_used=false`及无重复`training_case_ids`，只允许完整pilot10或完整fold0训练100集合。

A=4×4，A全部8例成功、有效采到4并发且逐例有内存样本、无门禁后才调度B=4×6。每档从1→2→4升档，每级需要实际资源样本和已完成SyN病例峰值依据；使用1.5倍采样峰值作保守RAM预测，不沿用仿射155MiB。每档最多8例、硬超时1800秒，最多16例；不预热、不重复、不重建模板。因升档包含低并发阶段，整体吞吐明确包含升档开销，不能称为纯稳态四并发吞吐。

benchmark运行依赖现有`psutil`和`nvidia-smi`，每约2秒记录系统CPU/RAM、全部GPU/显存（仅GPU0排除门禁，不干预）、单例子进程工作集/私有内存、磁盘字节速率及可获取的busy百分比。系统RAM/预测RAM或相关GPU/显存达到80%停止派发，CPU连续3次达到80%停止派发；GPU观测失败也停止。硬超时/取消仅按登记PID及创建时间核验本run进程树；不会按进程名结束其他任务。资源停派后运行中病例可自然结束，但硬超时仍生效。Python内部读图/检查按步骤协作停止，无法抢占正在执行的原生I/O；因此需在真实部署验收其超时行为，当前不能宣称严格墙钟上界已验证。

报告整体成功数/墙钟吞吐、各例耗时、启动序列、失败/未完成数量、CPU/RAM/GPU/磁盘均值/p95/峰值和逐例采样内存峰值。2秒采样可能漏过瞬时峰值，Python图像缓冲只反映于系统RAM；磁盘busy若平台不提供会明确UNAVAILABLE。只读记录既有相关进程名/PID；未接入既有训练供给日志时标记EVIDENCE_GAP，不能声称未影响他人。

B全部成功且数值门禁满足、吞吐至少高10%时只记`benchmark_candidate=4x6`，`benchmark_selection`仍保留4x4并等待成对几何/解剖质量审查。A后B存在缓存和负载混杂，仅为工程测速。未完成档禁止全量耗时外推。benchmark成功也不触发全量配置、QC认可、提交、部署或训练。

### 当前验收范围

静态核对覆盖官方来源、方法分支、变换/形状更新命令、线程环境、调度屏障、失败传播、状态计数与路径边界。轻量验证使用newconda，自带CIM做资源预检（该环境缺psutil，未安装）；旧测试完整保留。实际结果见本节末HANDOFF。真实部署ANTs未在PATH及本项目检索范围发现，因此Windows真实合成几何、非交换往返<0.25、解剖QC、资源监控/超时实测、A/B测速均NOT_RUN；独立Level3 PENDING。不得称本改动已获独立PASS或具备全量执行资格。


## 2026-10-29：fold0训练100次的最小适配

此节覆盖下方旧交付范围和历史NOT_RUN状态；下方其余来源、几何、路径与原始数据保护契约继续适用。旧10例配置缺省仍走`pilot10`，不改变其配准行为。新增`configs/fold0_train100.example.json`仅为准备配置，`execute_authorized=false`、`geometric_qc_accepted=false`，不构成全量启动授权。该源码快照已获独立Level 3代码静态审查PASS；真实几何仍为NEEDS_REVIEW。服务器部署和全量运行尚未执行。

- `input_cohort=fold0_train100`：严格使用既定fold0全部100次训练检查/27位患者；验证28次/7位患者仅核对身份分组，不读验证影像或GT。
- 复用原三项阶段A证据；新私有证据目录另加入`fold0_train100_identity_private.json`，通过新`FILE_SHA256.json`及配置的`evidence_index_sha256`固定。不要改写旧证据索引。全量manifest必须包含`status=SERVER_READONLY_VERIFIED`、固定`split_sha256`、`weighting=equal_check`、按case排序的100项`selected`及28项`validation_cases`。训练项沿用小样的路径、患者伪匿名、源文件SHA256与实际ITK头字段；固定10例身份和参考必须不变。
- 明确采用每次检查等权的候选（AverageImages mode2不变），不是每患者等权。多时间点患者贡献更多，实际分布与最终选择应在运行前确认。未实现任意加权、SyN或覆盖加权。
- 保持两轮、2线程、串行、seed、Rigid/Affine参数、初始质心、Z8/10、最终冻结后重新逐例注册和逆映回。100项计划包含300次注册、1814步；覆盖fraction分母和common阈值采用实际输入数。
- `plan`仍只读元数据/文件身份，不调用ANTs、不创建run；`execute`除既有显式授权外，还要求全量`geometric_qc_accepted is True`。这个显式确认字段不能替代实际几何证据。
- 本轮20项轻量合成测试通过，覆盖训练完整性/患者互斥/受信manifest/旧10例/覆盖数值/输出保护；没有用合成测试证明真实图像质量。测试命令为`python -B -m unittest discover -s tools/ants_atlas/tests -p test_full_cohort.py -v`，运行前须做资源预检。

后续应在独立审查与真实小样几何验收后，按本地审查→用户授权提交推送→服务器`git pull --ff-only`流程同步代码，不通过覆盖服务器未提交文件部署。准备配置保持禁止执行，服务器真实full prepare/plan与全量配准尚未运行。


状态：原最小实现源码与配置曾获独立静态审查PASS（2026-10-28）；其后README初始化偏离说明及本次最小Z参数补丁的最终diff独立静态审查均PENDING。历史静态PASS不覆盖本次修改，也不代表运行或几何验收。入口、plan、ANTs、合成几何、真实配准、数值结果、训练及资源预检全部 **NOT_RUN**。AST语法解析不能证明运行或几何正确。本轮不安装依赖、不执行入口（包括 --help）、不运行pytest。

只有三个交付文件：`atlas.py`、`configs/pilot.example.json`、本README。运行时依赖Python 3.10+、现有SimpleITK/numpy及Windows ANTs 2.6.5；不安装或升级它们。此工具是本地Windows适配，不是官方Bash脚本原样执行，也不表示CG-SAMR数值复现。

## 候选resolved设置

| 项目 | 显式候选 |
|---|---|
| 输入 | 已验收且固定的10位fold0训练患者，每人1次检查；禁止重选 |
| 维度/线程/并发 | 3D、每任务2线程、串行1任务 |
| 外循环 | 2轮，20次主循环配准 + 最终模板冻结后10次重新配准 = 30次 |
| 初始对齐 | 10次质心平移初始化；无额外初始刚体优化 |
| Rigid | Rigid[0.1]；MI权重1/32 bins/Regular/0.25；1000x500x250x0，阈值1e-6、窗口10；shrink 6x4x2x1；smooth 4x2x1x0 vox |
| Affine | Affine[0.1]；MI权重1/32 bins/None/全采样；100x100x70x20，阈值1e-9、窗口10；shrink 6x4x2x1；smooth 3x2x1x0 vox |
| 强度 | winsorize [0.01,0.99]仅用于配准度量，histogram matching关闭；无N4、无锐化 |
| 模板统计 | AverageImages mode=2（官方 normalized mean）；无覆盖加权 |
| 形状更新 | AverageAffineTransform（含刚体成分），对平均模板施加平均仿射的逆；Affine路径无位移场、无gradient-step操作 |
| 随机性 | 固定seed=20261028，ANTs参数及子进程环境记录；2线程不保证跨机器位级确定性 |
| 插值 | DWI/Atlas Linear，域掩膜 NearestNeighbor，域外0，图像float32 |
| 参考网格 | 使用既有ITK基础候选：面内256×256、原spacing/direction；可显式配置Z整数扩层，缺省0/0保持原几何；不是已验收覆盖 |

预算是候选，不是实际运行授权。示例 `execute_authorized=false`。配置中的协议值严格校验，未实现任意多模态/全100次/患者时间点权重/SyN/调参接口。30个配准任务各有两个优化阶段（60阶段）；另有10个质心变换、10次初始化重采样、3次平均、2次仿射平均/形状更新、20次循环DWI重采样、30次最终DWI/Atlas/覆盖重采样和Python图像检查/域生成/覆盖聚合。预计耗时/峰值内存未知。

## 来源对应与刻意偏离

官方固定来源：[v2.6.5 antsMultivariateTemplateConstruction2.sh](https://github.com/ANTsX/ANTs/blob/v2.6.5/Scripts/antsMultivariateTemplateConstruction2.sh)。只读快照SHA256：
`80441088eda2195b98286dc04db71d2878e41e3ea1f2d8f0db26d84bb6639d87`。
本实现不复制第三方源码，不包含ANTs二进制；外部ANTs按其发行包许可证使用。

| 官方位置/语义 | 本地对应/分类 |
|---|---|
| 1090–1109：先生成初始群体平均图，再把各输入质心对齐到该平均图后重新平均 | `centre`用SimpleITK MOMENTS的Euler3D初始化（零旋转，平移），并以选定参考患者的质心作为对齐目标，替代官方初始群体平均图质心；初始化工具替换和质心目标改变均为本地候选偏离。不能仅用MOMENTS与antsAI的工具等价性解释，后续须验证初始化位置、固定参考网格内的覆盖及初始截断 |
| 1536–1566：Affine路径为stage0 + stage1 + stage3 | `register`使用质心初始化、硬编码Rigid和配置Affine；脚本-q不控制Rigid；Affine全采样与显式MI为候选选择 |
| 295–359：summarizeimageset | `average`使用AverageImages 2；关闭脚本默认锐化是刻意偏离 |
| 362–470：shapeupdatetotemplate，无warp分支 | `mean_affine_*.mat`平均后用`[mean_affine,1]`对mean图像重采样；保持仿射形状更新数学结构，不做SyN |
| N4默认打开、默认4轮 | 本地关闭N4、2轮、显式seed属于刻意偏离 |
| 输出模板/初始化网格 | 使用固定ITK计算网格，拒绝让AverageImages自动扩展网格；本地候选，尚无覆盖验收 |
| 最后更新后的对应 | 新增10次冻结最终模板重新配准，不直接复用末轮更新前变换 |
| 覆盖 | 新增全采集网格域的最近邻映射和count/fraction/common；不改变模板统计权重 |

脚本的后阶段默认CC并非此工具选择的Affine MI；这是显式候选偏离。关闭N4/锐化不代表在DWI上已证明更优。exe哈希在执行快照中记录，服务器曾回传的2.6.5-gfdce4d2仅作未复核版本标注；本轮没启动任何exe。只需antsRegistration、antsApplyTransforms、AverageImages、AverageAffineTransform四个已在既有清单中的程序；其余工具不调用。bin/lib保持发行包结构；仅子进程PATH前置bin/lib以支持随包DLL加载，不修改永久PATH，DLL实际可用性NOT_RUN。

## 输入、信任与安全边界

直接读取既有私有验收目录，不新建重复患者manifest：

- `FILE_SHA256.json`：文件名到SHA256字符串的索引；由私有配置中的可信 `evidence_index_sha256` 固定该索引，然后逐项核对以下三个JSON。
- `stageA_acceptance.json`：COMPLETED / PASS_WITH_EXPLICIT_LIMITS，固定cohort及split哈希。
- `pilot_selection_v1_identity_confirmed_private.json`：只用selected中原10条dwi_path、身份、已验收SHA256和actual_ITK_header，不读取keep_mask_path/GT。
- `reference_grid_ITK_candidate_private.json`：核对参考身份及native_ITK_header，使用其reduced_inplane_grid_candidate。
- `--split`：原始五折文件，锁定SHA256 `cfcad46bf1f47d397cf7ea8b5c3a40c2a49db77767eb343c6e3a92e3e88831d4`；10项必须在fold0 train，28项val不读影像。

私有索引哈希必须由主代理对已验收文件确认后填写，不能给任意新清单计算哈希来替换受信名单。患者分组信任阶段A已验收、哈希固定的身份字段；此工具不做临床身份重新识别。execute开始前核对10项DWI字节SHA256；plan只读JSON/源码字节、文件存在性和路径元数据，不打开影像字节/头/体素，也不启动ANTs或导入图像库。plan可包含患者路径/身份，stdout必须保留在仓库外私有位置。

所有路径要求绝对路径。read_only_data_roots须覆盖完整原始/派生输入数据集根目录，不仅imagesTr；多个根需全部列出。输出根与源码仓库、数据根、验收目录、split/配置/参考脚本父目录、ANTs根均不得双向包含；run-id必须全新，不恢复/清理/覆盖已有run。不接受UNC/device路径、重解析点/符号链接、硬链接文件或含ANTs分隔符`,[]`的路径；使用resolve+Windows normcase处理现存8.3别名及大小写。不存在路径的别名、运行中外部并发替换/创建链接不提供安全保证：要求本地普通磁盘、单一写入者、无并发修改；未来需合成验证Windows别名/拒绝路径的行为。不得用管理员权限对不可信可写数据目录运行本工具。

执行命令为参数列表、shell=False，逐命令stdout/stderr合并独立日志，退出码非零、缺失输出、非有限值/常量DWI或几何不符立即失败；run.json保留配置、源码/输入/证据/exe哈希、Python库版本、任务及状态。源DWI只读，所有重采样/float32转换只写新run。执行前哈希/依赖门禁失败还没有创建run，错误由调用者终端保留；运行阶段失败标记FAILED且不删除部分产物。run.json和命令日志均为私有信息，不能提交Git。未实现失败恢复/重试。

## 几何、变换与覆盖

ITK size/spacing/index为XYZ，direction为flattened row-major 3×3，origin/物理坐标为LPS；数组为ZYX；不从JSON中的affine_ras直接构造ITK方向，不按切片序号对齐，不改输入NIfTI头或统一1mm。各患者13/16/18层原网格保留，Atlas映回时以原DWI为reference。输出实际NIfTI读回检查size/spacing/origin/direction及有限值/动态范围；转换为float32，不以PNG替代。

在antsApplyTransforms图像pull语义下，fixed=模板、moving=患者，输出仿射A用于患者影像到模板的重采样；物理采样点从模板LPS映至患者LPS。Atlas到患者原网格使用`[A,1]`，reference为原患者。该语义与点映射不能混用。Rigid+Affine由ANTs collapse为单个0GenericAffine.mat；无额外变换列表复合。平均仿射的逆仅用于模板形状更新，最后逐例A来自冻结后的template_02，不使用iter2的旧A。

扫描域是源采集完整体素网格上全1（包括去颅骨后为0的区域），假设该矩形网格全部为有效采集；不是非零DWI、3mm keep mask、脑覆盖或GT。若采集中含填充/缺片，该假设不成立，必须先明确实际域规则，不能自动改用keep mask。最终10个域用相同最终A及最近邻映射；输出coverage_count、coverage_fraction=count/10及coverage_common=count==10，并记录共同/联合覆盖体素数和共同域占参考网格比例。不同覆盖造成的平均偏差仍须质检，本轮不加入覆盖加权。

## 可选Z整数扩层（本轮仅静态检查）

配置 `z_pad_low`、`z_pad_high` 分别沿ITK索引Z低端和高端扩层，缺省均为0；只接受非负整数，拒绝bool、负数、浮点、字符串和null，不静默转换。prepare解析后的两个整数记录在resolved_config、执行config及reference步骤中；原受信grid不改。0/0保持旧reference几何及处理参数，元数据允许新增两个0字段。

令基础层数为Nz、Z间距为spacing_z、方向矩阵为D：新层数为 `Nz + z_pad_low + z_pad_high`，新LPS原点为 `origin - D[:,2] * spacing_z * z_pad_low`。移动的是三分量原点，低/高端不是世界Z方向；旧索引(i,j,k)与新(i,j,k+z_pad_low)物理位置一致。XY、spacing、direction和患者原网格不变；保留基础候选XYZ中心跨度校验，并在扩层后检查XY跨度、新Z层数及两个基础角点物理嵌入（绝对1e-4 mm、相对容差0）。图像库仍仅在execute中延迟导入。

8/10仅为旧变换限定集合Z包络的待选候选，不是默认值、最小脑覆盖、必要层数、新网格优化后的充分保证或全XYZ保证。16→34层只增加背景/采样空间，不增加真实层间信息；完整矩形59/60条XY越界及两轮逆采样XY不足的限制仍在。更大网格可能改变耗时、资源峰值和优化结果；注册任务仍为30次，参数及后续路径不变。

本补丁仅做静态检查，运行验证全部NOT_RUN；不自动选padding、不改旧受信网格/清单/旧run，不新增真实执行配置或重跑。未来选择8/10须使用独立新配置与全新run-id，另行确认资源和执行范围；输出继续受现有路径隔离及拒绝run复用约束保护。

## 将来服务器用法（本轮未执行）

先由主代理接受独立静态审查，再单独批准合成验证及其资源预检/容差/预算；通过后才能明确授权真实10例。以下路径全为脱敏占位，不能原样执行。私有配置置于仓库外，填现存路径与可信索引哈希；示例execute_authorized保持false直至获得运行批准。不要把本次实现当作真实运行授权。

```powershell
$py = 'C:\PRIVATE_ENV\python.exe'
$entry = 'C:\PRIVATE_SOURCE\DWI-Atlas\tools\ants_atlas\atlas.py'
$cfg = 'C:\PRIVATE_CONFIG\pilot.json'
$evidence = 'C:\PRIVATE_EVIDENCE\acceptance'
$split = 'C:\PRIVATE_DATA\DWI\splits_final.json'
# 仅规划：不调用ANTs、不读影像、不创建run目录。输出含私有信息。
& $py $entry --mode plan --config $cfg --evidence $evidence --split $split --output-root 'C:\PRIVATE_OUTPUT\atlas' --run-id 'pilot_new_id'
# 以下仅记录未来接口：需审查、合成几何验收、资源预检和真实预算批准。
# & $py $entry --mode execute --config $cfg --evidence $evidence --split $split --output-root 'C:\PRIVATE_OUTPUT\atlas' --run-id 'pilot_new_id'
```

本地静态语法检查仅用newconda的ast.parse读源码字符串，禁止import atlas、运行--help、py_compile生成缓存或调用任何影像计算。独立审查员只读这三个文件及固定官方脚本，对照HANDOFF逐文件SHA256（包括untracked）；变化须标记SNAPSHOT_CHANGED。静态PASS仅表示没发现静态阻断。

## 后续验证清单（全部NOT_RUN）

- 运行前CPU/RAM资源预检，80%门禁、2线程/串行预算和容差单独锁定；不采用历史负载替代。
- Windows路径大小写、8.3/junction/hardlink/UNC及输入输出双向包含拒绝；已有run拒绝；plan无exe/影像读取。
- exe启动/版本/DLL、完整命令参数和日志/失败中断/输入哈希失败、实际SimpleITK及ANTs文件格式互操作。
- 非单位spacing、斜direction、非零origin、不同Z的身份/已知仿射，正逆/复合/pull与点映射；MOMENTS与antsAI质心平移的一致性，以及参考患者质心相对初始群体平均图质心的差异对初始化、覆盖和截断的影响。
- AverageImages mode2的实际归一化、float32/有限范围、仿射平均取逆的形状更新、最终冻结后的映回与几何容差。
- 源域边界、最近邻与连续插值、count/fraction/common值、参考网格脑/扫描覆盖及背景平均偏差；未证实覆盖不得认定模板可用。
- 真实10例仅在新授权后逐例保留配准前后叠加/棋盘格、轮廓、覆盖及native映回对照，统一显示窗/物理层面，不能挑最好病例。本轮未实现绘图/报告系统，也未生成这些图。
- 退出0/文件存在/模板更锐利均不能自动判定配准通过；状态只能为COMPUTED_PENDING_GEOMETRIC_QC。真实小样/全100次/研究效果/训练NOT_RUN。

## HANDOFF — 2026-10-29

终态：COMPLETED（仅本地工程实现；运行放行仍未完成）。角色：本聊天唯一实施者，直接实现源代码与测试，原因是任务书明确指定手动唯一实施者并禁止自动创建代理/聊天；未创建子代理。执行器为当前Codex会话，底层精确模型ID未通过工具暴露，不虚构Luna/LunaMax身份。

仓库：`E:/study/研一/work14-图像分割/DWI-Atlas`。分支main；起止commit均为 `2907411641a84dda24ded2a089d71aab1f6732d9`，工作区改动未提交、未推送。开始时`?? docs/`原样保留。生产/测试/配置/说明文件均限于tools/ants_atlas；canonical project memory按任务书要求未修改。

行为：显式并行预算与SyN方法、每轮冻结模板、固定序归约/屏障、完整正逆链、官方physical-vector形状更新、实际成功计数、协调器原子receipt、失败停派、固定前8例有界A/B准备、禁执行示例。来源和偏差见本节前文；旧SOURCE_SHA及旧pilot/full配置、旧test_full_cohort.py均未修改。

验证：`D:/Anaconda/envs/newconda/python.exe -B -m unittest discover -s tools/ants_atlas/tests -p test_*.py -v`，最终25项通过（2.062s，exit 0）；其中20项原有、5项新增。最终资源预检CPU 22/21%、RAM58.90%、GPU0利用率7%、显存1315/6144MiB；采用极轻量单进程检查，不启动患者任务。`git diff --check`通过。首轮曾有字符串拼接错误，后续剩Windows路径断言差异，均已修复并通过最终检查；未掩盖失败。newconda缺psutil，首次Python资源预检失败后改用Windows CIM，未安装依赖。

未执行/未认可：真实Windows ANTs几何与超时验证、资源监测实测、A/B、患者配准、现有服务器仿射run完整性/几何验收、合成形变数值往返、独立Level3审查、解剖质量、小样放行、全量配置/启动包、训练、服务器同步。真实部署ANTs未在PATH及本项目检索范围发现；不宣称全机不存在。单机静态自审和上述25项测试不能替代真实非交换/斜direction/逆场验证。benchmark缺psutil会在任何病例派发前失败关闭。内部Python原生I/O不能被线程安全抢占，严格超时仍属部署验收项；磁盘busy不可用及训练供给观测缺口均会明确标记。

原始数据/固定split/旧输出：未写入、未重命名、未删除；本轮未读取真实患者图像内容。未干预现有服务器进程。下一步：将下列精确快照交独立只读审查；按后续明确授权与现有环境完成真实部署几何receipt，再进入有界A/B及成对几何验收。任何测试/benchmark/QC均不自动触发全量。

可审查代码/配置/测试/计划清单（不含本README，避免自引用）：

```text
tools/ants_atlas/atlas.py:a8b6687480d8cd305de72a5404d8754713bd3cc2ba4848c1ce0056441afcd600
tools/ants_atlas/scheduler.py:06dff310ae54131c74ff51c6574c63cefe268b95fa85c85060eeea2bbc275bf4
tools/ants_atlas/configs/syn_pilot.example.json:899d6870ad23fdd8abb7815cbab015ee96dab0b9ad429fe519e60f30788ab1c4
tools/ants_atlas/configs/benchmark.example.json:995b27b67df2abf5145e0d0067f83f45b82472a31286bb873a77538bd065cc0c
tools/ants_atlas/tests/test_parallel.py:14055c96b843ddfe72fb6dccc83d7ac109a8ba8f3b73de7845f24af0ad84b91a
tools/ants_atlas/tests/test_syn.py:745ed93b22ef0f29528426a4a982372fb9b429b46b341f84f29e9f97fbfe7e5e
tools/ants_atlas/FULL100_PLAN.md:484aca4d9a1675d8799e960d4b8807806830c6b5df38cde6a372b2c5a97e1435
```

清单SHA256：`1c5eae702e5a285691c4065cd51e0d266f2fc2d6ef2c98cc4ea48058b6d08d5f`。README最终文件SHA256由交付终端另列。

## 本次三项阻断修复（2026-10-29；覆盖上方历史验证/快照状态）

本节只记录本次 focused fixer 修复；历史静态 PASS、25 项测试及旧哈希不覆盖当前快照。没有独立 Level 3 复审结论，没有真实小样、部署几何或全量运行放行。本次仅修改 atlas.py、scheduler.py、本 README，新增 tests/test_receipts_resources.py；保留已有 SyN/并行实现、参数、变换顺序、形状更新、候选线程与预算。所有 example 的 execute_authorized 仍为 false；不生成或更新任何真实验收 receipt。

### 版本化 receipt 与信任链

真实私有 receipt 必须由已有可信运行证据及独立验收记录构成，不能用下面的结构说明冒充验收。入口配置先以既有 `syn_pilot_qc_receipt_sha256` 或 `benchmark_template_receipt_sha256` 固定整个 receipt 的字节哈希；各 pin 都严格为 `{"path": "绝对路径", "sha256": "64位小写SHA256"}`，路径本身不是身份。

共用字段：

| 字段 | 校验 |
|---|---|
| schema | 小样为 `syn-pilot-qc/v1`；测速模板为 `benchmark-template/v1`，旧无版本 receipt 拒绝 |
| status / gt_used / split_sha256 | 必须 PASS / false / 固定 SPLIT_SHA |
| run | 固定实际构建 `run.json` 的 path/SHA256 |
| geometry_acceptance | 固定独立几何验收 JSON 的 path/SHA256 |
| input_manifest | 固定构建所用受信 manifest JSON 的 path/SHA256；含原 `selected` 完整有序记录 |
| method_config | 与构建 run 的 `method_identity(config)` 完全一致，明确记录实际方法 |
| template_sha256 | 仅测速模板：还须等于配置固定的模板文件哈希与验收中同一输出的哈希 |

`run.json` 必须为 `COMPUTED_PENDING_GEOMETRIC_QC` 且有 ended_utc；completed_steps、total_steps、steps 长度一致且非零，tasks 数量相同且全部 SUCCEEDED。其 evidence_sha256 必须包含该 input_manifest 的路径/哈希及固定 split 哈希，inputs_sha256 必须与有序受信记录中 dwi_path→verified_511_file_sha256 逐项一致。未完成、失败、benchmark run 均拒绝。现存 computed 状态表示计算完成，独立几何验收仍以另一个固定 JSON 为准；不改写旧 run 的状态。

几何验收 JSON 结构为 `schema=atlas-geometric-acceptance/v1`、`status=PASS`、`independent_review=true`、非空 reviewer、`run_sha256`、`outputs_sha256`（绝对输出路径→SHA256）。run_sha256 必须精确匹配被固定的 run.json。必需输出为 `template_02.nii.gz` 以及全部构建病例的 `final_XX_dwi.nii.gz`、`final_XX_atlas_native.nii.gz`、`final_XX_coverage.nii.gz`。这些输出声明必须位于 run 目录内并匹配 run.steps 的 output_sha256；文件实际字节仅在 execute 前核验；验收记录中的其他输出也逐项检查。此校验核对的是已固定的验收证据，不能自行判定解剖质量，也不能证明任意自行编写的 reviewer 字段确为独立审查；信任根是外部认可并固定的 receipt 哈希。

### 小样与全量兼容规则

方法身份逐项包含 dimension、outer_iterations、seed、scan_domain、extra_initial_rigid、n4、sharpen、average_images_mode、rigid、affine、method、weighting、z_pad_low/high；SyN 还包含 syn、shape_gradient。省略的旧仿射 method/weighting/Z 字段按原有默认值解释。小样与全量的病例数、input_cohort、run-id、输出目录不属于方法身份，不比较两份完整配置字节。

当前没有允许跨线程/并发转移几何验收的证据，因此 threads 和 parallel_jobs 都必须与已认可小样相同；不同预算须单独完成验收，不自动继承。此规则不修改已有 1x2、4x4、4x6 候选或 A/B 预算。

小样必须是原 pilot10 的10项受信记录，完整字段和顺序一致，不只比较病例ID。其 atlas/source 与 scheduler SHA256 必须对应当前文件；exe_sha256 必须等于当前计划将使用的工具哈希，validated_tool_versions 必须等于当前已固定部署验收的 tool_versions。小样 run 及其 config 的 syn_validation_receipt_sha256 必须与当前配置相同，并存在于该 run 的 evidence_sha256；official_source.raw_sha256 必须为固定官方来源。当前 execute 仍先验证部署 receipt 对当前代码、工具与既有几何检查的绑定。stageA_acceptance、reference_grid_ITK_candidate、pilot_selection 三份证据的哈希也要与当前受信索引一致。代码、scheduler、工具、部署证据、方法、输入、顺序、参考网格或验收产物变化均使旧认可失效。

这层身份核对不重读全量患者源影像；复用固定 JSON manifest 和 run.inputs_sha256，plan仅核对所验收派生产物的身份声明；execute才核验其字节。原 execute 开始前源文件哈希检查仍保留，不代表额外授权患者运行。

### 测速模板来源

prepare 从固定构建 run 的 input_cohort 选择对照名单：pilot10 对照当前固定 pilot selection；fold0_train100 额外通过当前受信 FILE_SHA256 索引固定完整训练 manifest，再调用原 select_records 验证固定100项、分组、split与pilot一致性。比较 selected 的完整有序记录，拒绝同ID旧源文件、缺失、重复、额外或乱序；不靠集合包含关系放行。

模板构建方法可以是原已认可 rigid_affine，也可以是 rigid_affine_syn；method_config 必须准确对应其实际构建 run，而非强迫与测速使用的 SyN 相同。模板、split、GT未参与、构建输入与验收身份任一不符立即拒绝；不重建模板、不改写旧产物或 receipt。旧资料若缺少所需可验证记录，保持阻断，交外部验收者处理，程序不自动补发认可。

### GPU 失败关闭与验证结果

nvidia-smi 即使 exit 0 也要求非空设备清单，每行恰好四列；编号为非负十进制整数且不重复；利用率和显存值有限；util∈[0,100]、total>0、used∈[0,total]。GPU0 也必须有效，仅排除80%负载阈值判断。无效观测清空该次设备数据，记录具体 resource_stop 原因；首次观测失败派发0例，运行中失败停止后续派发，自有任务处理、进程身份保护与超时契约不变。测试仅 mock GPU 查询和资源系统接口，不启动 ANTs。

本次 newconda 单进程验证：

```text
D:/Anaconda/envs/newconda/python.exe -B -m unittest discover -s tools/ants_atlas/tests -p test_receipts_resources.py -v
17 tests, OK（3.251s）
D:/Anaconda/envs/newconda/python.exe -B -m unittest discover -s tools/ants_atlas/tests -p test_*.py -v
42 tests, OK（6.397s）
```

每次测试前均用 Windows CIM 短窗检查CPU/RAM并查询GPU。最终focused预检CPU28/20%、RAM69.25%、GPU0 1%、1289/6144MiB；受影响组预检CPU23/22%、RAM69.69%、GPU0 8%、1355/6144MiB，均低于80%。测试使用微型合成文件、元数据与原有微型图像测试。先观察到旧GPU逻辑失败和receipt接口缺失，补齐实现后通过；中间100例合成夹具输出不全的错误已修复，未隐藏失败。

未执行：真实ANTs/部署几何、小样患者配准、A/B测速、全量SyN、服务器操作、训练或独立复审。没有安装依赖，没有提交/推送，没有修改原始数据、固定split、旧输出或项目记忆。最终完整快照（含untracked）以本次交付终端/最终HANDOFF为准，README不内嵌自身哈希。

最终自查限制：HEAD旧串行仿射run没有tasks/total_steps及逐步骤output_sha256，当前严格校验会拒绝这种旧记录，即使方法是rigid_affine。新版完整记录的仿射模板已通过测试；旧串行记录兼容尚未完成。尝试补兼容的命令被自动批准审查拒绝，未执行，原因是缺失这些证据时放行会弱化验收。此项交独立复审明确证据规则；不得把上文“可以使用仿射模板”解释为任意旧receipt均可通过。本次最终状态BLOCKED（旧串行仿射兼容待决），测试仍为17/42通过，未声称独立Level3通过。

## 旧串行仿射来源兼容续修（覆盖上一段BLOCKED状态）

用户已明确批准独立事后验收规则，并授权接续另一对话的实现。仅 `validate_template_receipt` 接受 `legacy-affine-template/v1`；SyN小样与部署门禁不接受此schema，新格式run仍走严格校验。

receipt必须在仓库及旧run之外独立保存，含receipt_path、hash_collection=post_hoc、带时区collected_utc、status、gt_used、split_sha256、method_config、template_sha256，以及path/SHA256 pins：run、original_config、input_manifest、fixed_split、build_code、integrity_acceptance、geometry_acceptance；build_tools为历史工具路径到pin。原始配置与run.config核对，仅允许旧prepare补入缺省z_pad_low/high=0。源清单按当前受信完整顺序和逐例源哈希核对；历史代码/工具按旧run记录核对，不代表未来SyN工具已验证。

仅旧rigid_affine串行atlas且不存在新版tasks/total_steps可用。必须有命令步骤、完成计数等于全部steps且最终computed；逐命令exit_code必须为整数0、日志路径及固定哈希存在、日志首行argv匹配、工具身份已固定。完整性报告schema=legacy-affine-integrity/v1，必须独立PASS、非空reviewer、匹配run哈希、checked_steps覆盖每一步，含logs_sha256及outputs_sha256。几何报告schema=atlas-geometric-acceptance/v1，必须独立PASS并匹配run及完整输出哈希表。所有预期步骤输出和最终模板/逐例必要输出必须存在并匹配；已有历史输出哈希也不能冲突。缺证据即拒绝，旧记录不补写。事后哈希仅为当前身份，历史成功另由上述证据共同确认。真实报告缺失时无法认可，本轮未生成任何真实receipt。

续修验证：newconda `-B -m unittest discover -s tools/ants_atlas/tests -p test_receipts_resources.py -q`：34 tests OK（含继承的既有receipt回归）；同入口 `-p test_*.py -q` 结果见本次HANDOFF。focused预检CPU37%、RAM74.28%、GPU0 13%/1327MiB；affected预检CPU23%、RAM71.93%、GPU0 6%/1321MiB（总6144MiB）。全部为合成测试，没有真实患者、ANTs、服务器、A/B或独立审查。代码完成状态以测试最终结果为准，不宣称独立Level3通过。


## benchmark plan 字节边界修复

prepare中的benchmark_template原pinned、严格validate_run_receipt输出循环、legacy输出循环及模板自身sha已拆分：plan只检查路径存在/普通文件、安全边界、SHA256格式和JSON证据身份，不打开NIfTI字节，不导入影像库。main计划输出artifact_byte_validation=PENDING，计划通过不代表执行授权。固定JSON、旧日志、代码和工具身份校验保留。

execute在授权检查后、部署门禁及任何影像库导入/run创建/记录写入/监控/派发之前，重新固定receipt和受信manifest，强制核验模板自身及全部验收产物实际SHA256；严格和legacy来源均覆盖。无配置开关可关闭执行校验，也不接受plan通过标记代替本次检查。SyN部署和小样QC门禁保留。错误不写证据、不创建run。

新增test_plan_boundary.py经真实prepare/build/execute路径覆盖两种来源：拦截builtins.open及Path.open禁止任何NIfTI读取，禁止外部进程；模板/逐病例产物变更均在run创建前拒绝；合法产物路径完成字节核验后在仍需部署验收的门禁截断。全部合成字节，不启动ANTs。focused 1项含6组子用例通过；affected结果见HANDOFF。未做真实运行或独立复审。
