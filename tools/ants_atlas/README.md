# Windows ANTs affine Atlas pilot

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
