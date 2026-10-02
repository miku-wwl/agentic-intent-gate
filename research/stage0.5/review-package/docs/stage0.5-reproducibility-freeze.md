# Stage 0.5 — Reproducibility freeze

Date: 2026-10-02. Gate A: **PASS with one explicit import-export compatibility alias**. Native unmodified imports: **FAIL (7/8 PASS)**. Compatible imports: **PASS (8/8)**. Gate B then executed successfully. No reference tracked source changed.

Stage 0 的五份文档全部读取后作为起点；两个参考 checkout 的 SHA、依赖元数据、Stage 0 archive hashes 和相关 API 已重新验证。没有重新选择参考项目或使用其他项目的代码、环境、镜像。

## Selected environment

| Component | Selected Version / Source | Evidence | Reason | Deviates From |
|-----------|---------------------------|----------|--------|---------------|
| Python | CPython 3.13.9, Windows 11 build 26200 | `reproduction/evidence/gate-a-imports-compatible.json`; `.python-version`; `pyvenv.cfg` | 当前可用解释器，满足两份源 manifest；新 venv 不共享 site-packages | CDK AgentCore config 为 PYTHON_3_14；Lambda 为 PYTHON_3_13；未验证云解释器 |
| AWS reference | 2.0.0, local editable, `fbea687d73f1ab9021793e96e778c2373f3266ff` | checkout HEAD; AWS `pyproject.toml`; editable wheel metadata | 固定源码是 API 权威 | 旧 agent lock 中的 AWS 1.1.0 不采用 |
| TAC Python reference | 2.5.0, local editable, `5677eeb72a783de730923121f4ee6b01984faf09` | checkout HEAD; TAC `pyproject.toml`; editable wheel metadata | 满足 AWS >=2.3,<3 且保留指定源码 | 旧 agent lock 中的 TAC 1.0.1 不采用；不用任意 PyPI TAC |
| Strands | strands-agents 1.41.0 | Stage 0 manifest/hash; agent runtime lock; actual imports and Agent execution | 满足 AWS >=1.30、agent manifest >=1.40；已收集源码证据 | root AWS lock 与 deployment lock 是不同快照，不整体照搬 |
| AgentCore SDK | bedrock-agentcore 1.11.0 | Stage 0 manifest/hash; agent lock; actual SDK `/invocations` execution | 满足 >=1.4，包含参考实际调用的 runtime APIs | AWS root lock 的 1.8.0 未选；未将 SDK 当作 managed service |
| boto3 / botocore | 1.43.15 / 1.43.15 | Stage 0 manifest/hash; agent lock; dependency check | 已有配套版本，满足 AWS 约束 | 不执行 AWS application APIs |
| Twilio SDK | 9.10.9 | Stage 0 manifest/hash; agent lock;真实 RequestValidator | 满足 TAC >=9.8.3,<10；真实签名运算通过 | dummy token 与本地 URL |
| Pydantic | 2.13.4 / core 2.46.4 | agent lock; frozen lock; installed metadata | 满足 TAC >=2,<3；模型与 session 验证通过 | SDK class-based Config deprecation 保留 |
| HTTPX | 0.28.1 | agent lock; frozen lock; actual client serialization | 满足 >=0.27,<1，支持 MockTransport | HTTP 传输替换为本地 recorder |
| Analytics library | segment-analytics-python 2.3.6 | agent lock; metadata | TAC 直接依赖；禁用遥测 | 无 analytics export |
| FastAPI / Starlette | 0.139.0 / 1.1.0 | agent lock; installed metadata; TestClient runtime execution | 使用已有锁证据，满足 server extra；SDK 原生 ASGI 路由可执行 | 无真实端口或云 HTTP server |
| Uvicorn / multipart | 0.48.0 / 0.0.29 | agent lock; installed metadata | 固定 server extra 所需版本 | 不启动 uvicorn |
| websockets | 13.1 | agent lock; AWS optional constraint | 满足 AWS >=13,<14，最小依赖偏差 | 不加载 TAC OpenAI realtime extras（要求 >=14），不运行 Voice |
| AWS typing package | mypy-boto3-bedrock-agentcore 1.43.17 | agent lock; installed metadata | AWS agentcore extra 必需 | 不安装完整两仓 dev/examples/docs 组 |
| Test dependencies | pytest 9.0.3; pytest-asyncio 1.3.0; pytest-cov 7.1.0 | existing lock evidence; reproduction dev group | 满足实际测试需求 | 未运行 lint/type/full suites，无覆盖率全项目声明 |
| Build environment | hatchling 1.28.0; packaging 26.2; pathspec 1.1.1; pluggy 1.6.0; trove-classifiers 2026.9.21.13 | pyproject build constraints; isolated builder resolution | TAC 要求 hatchling<1.30；构建及传递依赖均明确冻结 | hatchling/trove 初次 isolated build 解析后固定；不是 runtime upgrade |
| Package manager | uv 0.11.16 (`135a36367`) | command execution and baseline inspection | 已安装，可锁定 artifact hashes 与本地源 | 新建环境，无全局 pip install |

完整 80 个已安装 distribution 版本见 `reproduction/evidence/gate-a-imports-compatible.json` 的 `versions`；`gate-a-packages.log` 是独立包清单。冻结 lock 解析 82 个条目，包括本地源/虚拟项目和平台选择，不代表安装了 82 个 runtime distribution。MCP 1.27.1 是 Strands 的传递依赖；没有 MCP 服务、工具、应用集成或最终架构。约束列表包含未安装的历史锁条目（例如 OpenAI）；约束条目不等于已安装或已使用。

## Metadata precedence and mismatches

1. 两个 checkout 的 manifest 和实现优先。AWS manifest 是 2.0.0 / TAC >=2.3,<3；TAC manifest 是 2.5.0。agent deployment manifest 已要求 AWS 2.0.0 和 Strands >=1.40，其 lock 却仍有 AWS 1.1.0 / TAC 1.0.1。**没有把旧 deployment lock 当作完整可执行环境，也没有以旧 PyPI 包替换 checkout。**
2. Deployment lock 中的外部库版本已经由 Stage 0 wheel/source 证据覆盖、满足当前源码约束；因此选取 Strands 1.41.0、SDK 1.11.0、boto pair 1.43.15、Twilio 9.10.9。对于它没有的外部库，参考 TAC lock 的已锁版本。`candidate-pins.json` 对 124 个候选约束逐项记录来源；只安装当前 slice 需要的部分。
3. AWS root lock / TAC root lock / agent lock 属于不同快照；不直接 `uv sync` 任一参考工程。TAC 默认 examples/dev 组和 realtime extras 还会引入不需要的功能及 websockets 冲突；本 reproduction 使用自己的 project、精确 runtime pins 和有限 dev group。
4. Lambda requirements 只锁 AWS package 2.0.0，无法冻结传递依赖。CDK 选择外部 CodeZip Python packager；Stage 0 已追到 `PythonBundledCodeZipAsset.packageCode` 与 uv 安装/ARM64 bundling。这里没有执行 CDK、Docker bundling、synth/deploy，也不能从我们本地 lock 推断真实打包器会使用它。
5. CPython 3.13.9 是本地可用且被 manifest 支持的选择。CDK runtime 3.14、Lambda 3.13 与 Windows 路径/ABI 差异明确保留，不宣称部署环境完全复现。

## Demonstrated source incompatibility and exact glue

AWS `src/tac_aws/proxy/agentcore_lambda.py:39` 导入 `tac.channels.voice.twiml.generate_twiml`。指定 TAC checkout 的旧模块不存在该导出，只有 builder；真实实现位于 `src/tac/channels/voice/conversation_relay/twiml.py:92`，由 `tac.channels.voice` 公开导出。

这是指定源码之间的导出路径不一致，不是可以靠试新外部版本消除的问题。只尝试了一套有证据的 runtime candidate。原始 ImportError、7 个其余成功符号和 socket=0 见 `gate-a-imports.json`；AST/source hash 见 `source-contract.json`（这是添加 alias 前的诊断，里面 `shim_or_source_patch_applied=false` 和 `gate_b_started=false` 是当时状态）。

`reproduction/src/twilio_local_ref/compat.py` 将 canonical 的**同一个函数对象**赋给旧模块属性，拒绝替换不同的已有属性，并断言对象身份。没有复制函数、改参数、改 callback、改错误处理或执行 Voice。新的 compatible evidence 记录 alias、canonical source、signature、identity=true 和 8/8 imports PASS。此 alias 是本地基线的一部分；省略 bootstrap 时仍会失败。没有隐藏原生不兼容，也没有修改五份 Stage 0 文档。

其他 warnings：VoiceChannelConfig/TwiMLOptions deprecations、SDK Pydantic Config/utcnow deprecations；它们不阻断消息 slice，未修改参考实现。

## Exact freeze and installation commands

Working directory: `D:\workshop\sep\bedrock-research`.

```powershell
# 首次候选生成；现在已有 .venv，勿重复执行。
python -B reproduction/scripts/create_candidate.py
& .\reproduction\scripts\run_gate_a.ps1 -Step lock
& .\reproduction\scripts\run_gate_a.ps1 -Step sync
& .\reproduction\scripts\run_gate_a.ps1 -Step check
& .\reproduction\scripts\run_gate_a.ps1 -Step dependency-check
& .\reproduction\scripts\run_gate_a.ps1 -Step packages
& .\reproduction\scripts\run_gate_a.ps1 -Step imports
python -B reproduction/scripts/inspect_source_contract.py
& .\reproduction\scripts\run_gate_a.ps1 -Step compatible-imports
```

Wrapper 实际执行的 install 是：

```text
uv --no-config --cache-dir D:\workshop\sep\bedrock-research\.cache\uv sync --project D:\workshop\sep\bedrock-research\reproduction --frozen --python D:\anaconda3\python.exe
```

锁文件：`reproduction/uv.lock`；定义：`reproduction/pyproject.toml`。Wrapper 设置 UV_PROJECT_ENVIRONMENT=.venv、UV_PYTHON_DOWNLOADS=never、UV_NO_CONFIG=true、TEMP/TMP=.cache/tmp、PYTHONDONTWRITEBYTECODE=1、PYTHONPATH 为空，并在每步验证两个 SHA 和 tracked/index clean；不 checkout/reset。所有具体 executable/arguments/cwd/exit code 见 `commands.jsonl`；完整工具输入见 `task-tool-inputs.jsonl`。

第一次构建后补齐 isolated build pins，再执行 lock/sync/check/dependency-check 均成功；frozen sync 最后审计 80 包。首次短日志名曾被同一步重跑覆盖，命令历史仍保留；随后改为唯一时间戳日志。没有把重跑输出伪装成初次输出。

## Determinism and boundaries

依赖解析在当前 Windows/CPython 版本、固定 source SHAs、精确 runtime/transitive/build pins 与 artifact hashes 下可重复；后续用 `--frozen` 避免重新选择版本。源 SHA 不写入 editable lock，故 wrapper/runner 另行强制核对。`uv lock --check` 和 `uv pip check` 成功。没有在另一台机器/OS 或从全空网络缓存重装，故跨平台/长期 index 可用性 **NOT VERIFIED**；editable build bytes 和运行 timestamps/request IDs/object identities 不要求 bit-for-bit 相同。

网络下载只用于公开 package installation。Gate A import guard 外部连接尝试为 0。真实 AWS、Twilio、Bedrock、Secrets Manager、云文件系统、CDK 打包均未调用。这个 freeze 可运行本地消息基线；不是云部署验收。
