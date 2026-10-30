# Dataset511 SyN 与并行执行设计及验收任务书

日期：2026-10-29，Asia/Hong_Kong。
状态：DESIGN_ONLY；未实现、未测速、未授权全量 SyN。本文不是启动脚本。
基准仓库：DWI-Atlas，main，2907411641a84dda24ded2a089d71aab1f6732d9；编写前工作树干净。
最近服务器状态来自用户回传：RUNNING，1617/1814，runtime_validation=NOT_RUN；无采样时间，不视为实时状态。

## 1. 目标、选项与边界

推荐：沿用现有 Python 计划/执行器，先增加病例级并行，再增加明确的方法分支 rigid_affine_syn 与完整群体形状更新。优点是保留 Windows、数据身份、安全路径及私有证据契约；风险是必须独立验证变换链。
替代一：直接包装官方 Bash 模板脚本。方法出处清晰，但 Windows shell/依赖、路径和当前安全契约迁移成本较高，本轮不选。
替代二：只在最终仿射模板上做一次逐例 SyN。实现较小，但不是非线性群体模板更新，不满足本轮目标。

现有 rigid_affine 默认行为、旧配置、2轮/2线程/串行保持兼容。新方法显式配置，不静默改变旧启动包。
数据只用固定 fold0 训练100次/27位患者；验证28次/7位患者不参与模板、归一化、参数选择或筛选。固定 split SHA256 cfcad46bf1f47d397cf7ea8b5c3a40c2a49db77767eb343c6e3a92e3e88831d4。
原始数据只读，GT 不进入配准。每检查等权、Z低8/高10、原始源网格回映、AverageImages mode2、seed=20261028、N4和锐化关闭均不变。不得把支持域覆盖当作脑组织覆盖。
不修改现有服务器进程、线程、配置、代码或输出；不自动下载原始 NIfTI，不启动生成器训练。

## 2. 来源及必须解决的版本问题

论文：https://pmc.ncbi.nlm.nih.gov/articles/PMC8543492/ ，III.A 明确训练集群体平均Atlas及multivariate template construction；确切SyN命令、轮数未公开。本文参数是项目候选，不是作者配置。
官方固定版本：https://raw.githubusercontent.com/ANTsX/ANTs/v2.6.5/Scripts/antsMultivariateTemplateConstruction2.sh 。实施前保存独立参考副本和来源/原始字节SHA256，核对所有涉及的命令，不引用漂移的main默认值。
本机 CG-SAMR-reference/ANTs/Scripts 同名文件当前 raw SHA256=c07f5669e63dd983a9692dcc100e3ba93f5a42e43fe12e931f72959cb2b45e96，LF归一化SHA256=35c8d6b9a63ada529e83173b8a2d2193bca3d6b08375314a08cbff105f37cbcd，均不等于旧SOURCE_SHA=80441088eda2195b98286dc04db71d2878e41e3ea1f2d8f0db26d84bb6639d87。
禁止直接改旧SOURCE_SHA绕过核验。先确认旧固定副本位置，再为新方法建立独立参考身份；现有运行不受此本地差异判定影响。

## 3. SyN候选协议

方法配置 rigid_affine_syn；外循环2轮是本项目控制变量选择，不是论文规定。每轮对原始训练DWI执行Rigid+Affine+SyN；每轮所有病例共享冻结的同一模板。
Rigid和Affine保持现有配置。初始非线性候选：SyN[0.1,3,0]，CC[fixed,moving,1,4]，收敛[100x100x70x20,1e-6,10]，shrink=6x4x2x1，smooth=3x2x1x0vox，float32，Linear。此候选需实施时逐项对照固定官方注册调用与部署ANTs可用参数，不得无记录替换。
从与基线相同的质心初始化/平均模板开始，不默认复用已有最终仿射模板，以避免混入不同初始化协议。
每例保存Affine、Warp、InverseWarp及注册输出；不把Warp取负冒充InverseWarp。
令F=固定模板、M=原始病例。图像重采样候选顺序：M到F为 -t Warp -t Affine；F到M为 -t [Affine,1] -t InverseWarp。这里是antsApplyTransforms的图像pull契约，不可直接套用于点坐标。必须由非交换合成变换测试和实测部署工具验证后锁定。
群体更新对照固定官方shapeupdatetotemplate：稳定病例序平均warped影像；平均官方所用Warp字段并乘负shape步长；平均Affine；将平均字段按逆平均Affine以physical-vector语义变换；按官方复合顺序更新影像。shape_gradient=0.25为候选，四次字段应用须按固定版本核实；不能把四次复合静默改成字段乘4，也不能只做逆平均Affine。
字段必须为3D物理LPS位移向量（mm），有限值、正确分量/网格；average mode0用于位移场，禁止影像强度归一化作用于向量场。
冻结最终模板后重新向所有病例注册，按完整逆链回映原始网格；coverage仍使用源采集网格全1域及NearestNeighbor，不用GT。
最终状态仍为COMPUTED_PENDING_GEOMETRIC_QC；步骤数动态生成，不能沿用1814作为新协议总数。

## 4. 并行与失败契约

新增threads和parallel_jobs严格正整数验证（拒绝bool）；允许的首轮已准备组合为1×2、4×4、4×6，不开放自动扩大预算。
按每轮、每例组织任务组：注册→影像重采样→检查；不同病例并行，同例依赖顺序执行。轮末屏障之后才做平均与形状更新；最终对应阶段同样独立并行。
每例独立输出前缀、日志和子进程环境。ITK线程与其他数值库线程策略写入receipt，防止嵌套线程池放大；不改全局永久环境。
只有协调器写run.json，临时文件与os.replace原子发布；记录每任务ID、状态、PID、开始结束、退出码、版本、线程、输入/输出身份。completed_steps只能代表真实成功步骤，并行时另列active_tasks，不能用最大步骤ID冒充完成数。
平均和汇总始终按固定manifest顺序，不按任务完成顺序。线程变化不承诺位级一致。
任一任务失败停止派发新任务，不执行依赖平均；已运行的自有子任务允许自然结束并收集结果，run最终FAILED，保留全部证据；不自动重试、清理、续跑或杀其他任务。用户取消与资源停止只管理本run登记的子进程。
输出根规范化，拒绝与原始数据、证据、代码、旧run重叠。run-id唯一，拒绝已有目录。

## 5. 有界测速方案（准备完成后仍需明确启动授权）

固定现有pilot10清单的前8例（不按效果挑选），锁定清单顺序及SHA256；不创建任意新cohort绕过prepare。benchmark作为显式非正式模式，读取已验收pilot10并只调度固定8例。
使用同一个固定训练模板和相同SyN注册参数；不在每档重建模板。测完整病例任务组，不只计antsRegistration。
A：4并发×4线程，8例=两批；B：4并发×6线程，同8例，A满足门禁才执行B。最多16例任务执行，不自动重复/预热/追加配置。无调参搜索。
每档墙钟硬上限30分钟，合计60分钟；超时标记ABORTED_NONFORMAL并保留输出。结束自有进程必须通过已记录PID树核验，禁止按进程名批量结束。
启动前采样系统CPU/RAM、相关GPU/显存、现有训练进程状态；运行中2秒采样，记录均值、p95、峰值和单例峰值工作集/私有内存、磁盘负载。系统RAM（含预估并发峰值）与相关GPU/显存任一达到80%即停止派发，CPU连续3次达到80%亦停止派发；硬超时仍生效。GPU0按此前明确约定排除门禁但不得干预，标准ANTs不使用GPU。
SyN峰值未知时不能凭仿射155MiB峰值推算安全。首次并发分批升至1→2→4，每级至少一次有效资源样本；若不能可靠估算或资源不够，不升到4，记录本档未完成，不冒充4并发结果。
现有训练数据供给只读观察可用的既有日志/进程信息，不开启新训练采样器；若无法观察，报告此证据缺口，不能声称不影响他人。
吞吐=完整成功病例数/整体墙钟；同时报告每例时间、失败/取消数及启动顺序，A后B顺序可能受缓存和系统负载影响，只作工程测速。
只有B全部成功、无门禁、几何可比且吞吐至少比A高10%，才暂选4×6；否则保留4×4。任一档未完成不外推全量耗时。

## 6. 验证与放行清单

- [ ] 保留旧测试tools/ants_atlas/tests/test_full_cohort.py，证明旧默认计划和串行契约未回归。
- [ ] 新tests/test_parallel.py：最大并发/每任务线程、乱序完成但稳定汇总、轮间屏障、同例依赖、失败不平均、原子状态、输出冲突与路径边界。
- [ ] 新tests/test_syn.py：注册产物/命令、非交换Affine+平滑可逆Warp的正逆链、LPS非单位spacing/斜direction、physical-vector平均与形状更新、源空间网格恢复、覆盖mask二值。
- [ ] 合成形变在内域验证复合往返误差小于0.25个最小spacing；边界插值误差单列，不能靠放宽容差掩盖顺序错误。单独验证身份场与纯平移的解析期望。
- [ ] Windows部署ANTs真实合成运行验证，而非仅mock命令；检查Jacobian非正值、非有限值及范围。向量文件/逆场缺失必须失败。
- [ ] 测试前按resource-aware-testing预检；优先newconda，不安装升级依赖，不启动真实患者计算。focused→affected；真实ANTs缺失就标记未验证，不宣称PASS。
- [ ] 独立Level3只读审查最终精确diff/文件SHA256；审查者不实施、不嵌套代理，返回PASS或BLOCKING。主代理或实施者自审不算独立审查。
- [ ] 现有仿射100例计算完成且完整性核对；几何验收保留全部病例，不只挑好图。新全量导出原图范围需另行授权。
- [ ] 授权真实小样测速后执行A/B；回查几何、非正Jacobian、截断/轮廓与实际拟生图层面。数值测试不等于解剖准确。
- [ ] 小样方法验收后再准备100例全量配置，execute_authorized和geometric_qc_accepted默认False；记录认可对象与证据，旧仿射QC不自动认可新SyN。
- [ ] 提交/推送/服务器同步与全量运行须获对应授权，服务器git pull --ff-only前确认干净且旧run已退出。任何全量执行按钮或脚本不得由测试、benchmark或QC自动触发。

## 7. 实施文件与手动派发

生产范围：tools/ants_atlas/atlas.py；仅在职责清晰时新增同目录的小型scheduler.py。配置放tools/ants_atlas/configs，新增syn_pilot.example.json和benchmark.example.json（均禁执行）。文档更新同目录README.md与FULL100_PLAN.md；测试放同目录tests/test_parallel.py、test_syn.py。不重构无关模块，不修改segementation。

手动实施角色：唯一实施者，建议LunaMax普通新聊天；沿用本仓库main顺序开发，不自动建分支/worktree。不得自动创建任何worker/reviewer或递归代理。主代理此轮只准备文档，不接管生产实现。
可直接发送提示词：

请在 E:/study/研一/work14-图像分割/DWI-Atlas 接续实施docs/SyN_parallel_execution_design_20261029.md。起点main@2907411641a84dda24ded2a089d71aab1f6732d9，开始重新核对Git状态并保护其他改动。读取适用AGENTS.md及scientific-experiment-integrity、paper-reproduction-fidelity、resource-aware-testing和test-validation技能。按本文先完成并行调度再实现SyN，保持旧默认兼容；先解决官方脚本来源差异，不放宽旧哈希核验。所有shell直接申请沙箱外批准，Python优先newconda。当前服务器Atlas仍在运行，不连接修改服务器、不启动患者任务、不测速、不推送、不自动提交，不读取凭据或下载原始影像。仅修改本文列出的生产/测试/示例/说明文件；不改项目记忆、原始数据、固定split、旧输出、其他仓库。不得创建代理或聊天。完成工程实现和可用的合成验证；对缺失工具导致未执行的验证明确标记，不把mock测试当真实ANTs几何验证。遇到范围内测试失败应继续修复。最终返回唯一终态HANDOFF：COMPLETED/BLOCKED/FAILED、角色与可验证模型、仓库/分支/起止commit、改动文件与SHA256、行为、参数来源与偏差、测试命令/结果/未执行项、Git状态、原数据状态、未解决项、下一步。不得宣称通过独立Level3或已可全量执行。完成后把最终HANDOFF原样粘贴回主代理。

独立审查后由主代理准备实际服务器启动包：固定部署commit、规范化源码哈希、工具版本/哈希、私有manifest/config哈希、只读预检、默认禁执行开关及唯一输出目录。实际命令须由已实现CLI生成plan并核对，本文不编造当前不存在的CLI参数。

## 8. 当前完成度与下一步

已完成：设计、预算、文件边界、验证和放行规则、手动实施提示词。
尚未完成：生产代码、合成测试运行、独立审查、真实测速、SyN小样质量验收、服务器启动包。
因此仿射结果验收后可进入已准备的实施/测速流程，不能把本文称为已经具备立即全量执行的能力。
