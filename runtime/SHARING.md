# 共享流程

在「共享」页面选择已保存流程并发布。发布固定到该版本，后续编辑与保存需要再次发布才会对外生效。用途、输入和交付说明来自流程定义。共享设置管理本机名称、是否接受外部连接和并发数，默认最多同时执行 2 个外部任务，最多排队 100 个。

调用方通过「插件 → 添加插件 → Loops 客户端」发现附近的 Loops，也可填写发布端的 HTTPS 地址。双方核对配对码，发布端在「共享 → 访问授权」中同意后建立持久授权。每个授权允许访问全部已发布流程，包括以后新增发布的流程。撤销授权立即禁止后续访问并取消其排队任务。正在运行的任务可由本机在调用记录中停止。被拒绝、撤销或过期的连接可在插件详情中重新申请，必须再次核对并同意；复用原连接和历史记录。Shell 再次执行 connect 到同一主机会复用连接，失效授权会重新进入待确认状态。

停止共享后流程仍保留在列表，可直接恢复原先发布的版本；新保存版本需要明确更新发布。恢复单个流程不会开启已关闭的外部连接总开关。发布时若要同时开启，会在界面明确说明。停止共享单个流程或关闭外部连接后，已接受任务继续执行。完全退出 Loops 会停止运行。重启恢复尚未开始的队列，已经启动的任务保留中断或停止状态，不自动重新执行。调用端重试使用原 requestId，避免重复执行。相同 requestId 携带不同请求内容会被拒绝。

## Agent 与自动化任务

连接获准后自动接入插件，可在该插件的管理窗口查看和试运行远端流程。可在 Loop 或自动化任务对话中通过 `/` 选择。工具提供发现流程、提交任务、查询任务、取消任务和下载文件，调用方 Agent 根据流程说明选择要调用的流程。

`submit_flow` 返回异步任务标识，调用者须通过 `get_task` 查询到终态。仅 `success` 表示执行完成。JSON 产出直接放在 `outputs` 中，文件通过 `download_file` 下载到调用端。较大的 JSON 产出仅提供文件，避免把大响应塞进模型上下文。

任意支持 stdio MCP 的本地 Agent 客户端都可以使用下述命令启动桥接。`--data-dir` 与 `--peer` 指向已完成配对的本机配置。无需把访问凭证填进 MCP 配置。

```sh
/path/to/python /path/to/loops/runtime/loops_runtime/sharing_client.py \
  --data-dir /path/to/client-data --peer peer-xxxxxxxxxxxx mcp
```

## Shell 调用

从仓库根目录运行，首次创建独立调用方配置。`discover` 使用局域网多播，搜索不到时可以直接使用发布端共享设置中的地址。

```sh
runtime/.venv/bin/python runtime/loops_runtime/sharing_client.py \
  --data-dir desktop/.local/shell-client discover

runtime/.venv/bin/python runtime/loops_runtime/sharing_client.py \
  --data-dir desktop/.local/shell-client connect https://主机:端口 --name '我的脚本'
```

在发布端核对配对码并同意。记录命令返回的 `peer`，然后查询流程与版本。

```sh
runtime/.venv/bin/python runtime/loops_runtime/sharing_client.py \
  --data-dir desktop/.local/shell-client --peer peer-xxxxxxxxxxxx flows

runtime/.venv/bin/python runtime/loops_runtime/sharing_client.py \
  --data-dir desktop/.local/shell-client --peer peer-xxxxxxxxxxxx \
  run loop-xxxxxxxxxxxx --revision 1 --inputs /path/to/inputs.json \
  --request-id order-2026-001 --wait

runtime/.venv/bin/python runtime/loops_runtime/sharing_client.py \
  --data-dir desktop/.local/shell-client --peer peer-xxxxxxxxxxxx \
  download shared-xxxxxxxxxxxx 0 /path/to/result.json
```

输入 JSON 使用流程声明的输入 ID。文本直接传字符串。CLI 中的文件可写成 `{"source":{"file":"/path/to/input.csv"}}`，CLI 读取文件内容并上传，发布端不会把调用者传来的字符串当成本机文件路径。每个输入文件最多 10MB，文本最多 100000 字符，整个请求最多 15MiB。`task <任务ID>` 查询进度，`cancel <任务ID>` 取消任务。`run --wait` 等待真实结果，失败、取消或中断以非零退出码结束。

## 接口与身份

独立共享接口使用 HTTPS。证书与客户端凭证保存在各自数据目录的 `sharing/` 下。服务端保存令牌哈希，客户端凭证文件权限为 0600。配对码绑定服务端证书和随机凭证，后续请求先核对已保存的证书指纹，再发送凭证。证书变化时必须重新配对。

本机管理操作仅通过已有 IPC 接口进行，网络接口只能访问下列操作。POST `/v1/action` 接受 `{"method":"...","params":{...}}`。

| 操作 | 授权 | 内容 |
| --- | --- | --- |
| `identity` | 无 | 主机 ID、名称、端口、协议版本 |
| `pair` | 无 | 名称与客户端生成的随机 token，返回待审批状态及配对码 |
| `pair_status` | 提供申请 token | 查询该申请状态 |
| `flows` | Bearer token | 已发布流程的用途、输入输出与版本 |
| `submit` | Bearer token | flowId、revision、inputs、requestId，返回持久任务标识 |
| `task` / `cancel` | Bearer token | taskId，仅允许操作本客户端的任务 |
| GET `/v1/files/{taskId}/{index}` | Bearer token | 流式下载已声明的最终产出 |

网络文件输入为 `{"name":"input.csv","base64":"..."}`。结果包含任务状态、JSON 产出与可下载文件引用，不暴露流程脚本、资源配置、运行日志或主机文件路径。详细失败证据在发布端「共享 → 选择流程」的右侧调用记录中展开查看。发布端可按流程筛选并翻页查看历史；停止共享或将流程移入回收站后仍可查看记录和结果。本地详情与文件预览接口不对外部调用者开放。

局域网发现使用 TTL 为 1 的 UDP 多播 `239.255.76.80:45871`，查询内容为 `LOOPS_DISCOVER_V1`，服务以 JSON 单播响应基本身份信息。MCP 通过上述 stdio 桥接使用已授权连接，HTTPS 接口是 Loops 的任务协议。防火墙需允许 Loops 的监听端口和局域网多播。跨物理计算机的连接仍需在实际局域网环境中验收。
