# Agent 插件

Codex、MiMo 与后续客户端使用同一个插件协议。核心只管理发现结果、默认配置、运行绑定和会话生命周期，不按品牌调用不同代码。

## 安装与扩展

- 内置插件：`runtime/loops_runtime/agent_plugins/<插件目录>/`，会随正式客户端打包。
- 独立安装：`~/.config/loops/agent-plugins/<插件目录>/`。放入插件后重启 Loops，即自动加载，无需编辑注册表、核心流程或界面。
- 每个插件包含 `manifest.json` 和 `plugin.py`，可以携带私有辅助模块。相对导入如 `from .helpers import ...` 可用。
- 插件是本机可执行代码，只从上述安装目录加载，不从 Loop 工作目录、输入或模型生成文件中加载。安装目录中的插件应来自用户信任的来源。依赖使用标准库、已有运行时依赖或插件自带的模块；加载器不自动运行安装脚本。

```json
{
  "apiVersion": 1,
  "id": "example",
  "name": "Example Agent",
  "version": "1.0.0",
  "command": "example-agent",
  "paths": [".local/bin/example-agent"],
  "modes": ["read-only", "write"]
}
```

`id` 是稳定的流程引用，不能与已安装插件重复。`version` 是插件版本，客户端版本由探测返回。`paths` 相对用户主目录；默认发现还会检查 PATH 和常见安装位置。无需让用户填写命令、路径或启动参数。

## 接口 v1

`plugin.py` 导出：

- 可选 `locate(manifest) -> str | None`：特殊客户端发现方式；省略时使用通用命令查找。
- `async probe(binary, capture) -> dict`：返回 `ready`、`detail`、`version`，可附 `authenticated`。只检查安装和登录，不发起模型任务。共享 `cli_version` 适用于支持 `--version` 的客户端，其余插件自行解析。
- `async catalog(binary, capture, *, include_defaults) -> dict`：返回 `models`；每个模型包含 `id`、`name`、`reasoningLevels`，可附 `provider`、`defaultReasoning`。仅 `include_defaults=True` 时读取并返回 `nativeModel`、`nativeReasoning`；之后只刷新可选列表。
- `Session(cwd, emit, *, binary, read_paths, model, reasoning)`：返回异步上下文管理器，提供 `async ask(prompt, *, readonly=False) -> str`。进入和退出负责资源生命周期；退出和取消必须终止任务。`emit(kind, text, **extra)` 上报真实事件，会话建立时发出带 `threadId` 的 `session` 事件。必须落实声明的权限模式、已登记只读资料边界、模型及强度参数；异常不能转成成功回复。

`capture` 以参数数组执行命令，使用受限环境并提供超时清理；不要把宿主环境中的模型密钥传给客户端。公共状态、模型目录和事件不得包含凭证、完整供应商配置或原始认证响应。内置插件只返回所需元数据。

## 自动配置与兼容

首次初始化读取客户端默认模型和强度；缺失时取模型列表第一项及 low（不支持 low 则取支持的最低档位）。保存的明确选择不会被刷新覆盖。没有模型列表时等待成功探测后补齐配置。切换模型和强度复用统一设置界面，无需编写插件专用 UI。

新流程以已发现客户端作为初始执行项，Loops AI 根据任务与实际能力完成选择；员工明确指定的 Agent 优先。历史 `tool=codex` 及旧默认配置仍保持兼容。运行记录保存插件版本、客户端路径/版本、模型及强度。

插件加载失败会在设置中显示，其他插件继续工作；重复 ID 不覆盖已有插件。更新或移除插件后重启生效，正在运行的进程不热替换适配器。

## 验证

`tests/test_agent_plugins.py` 通过独立插件目录验证发现、模型初始化、默认选择、流程保存、CLI 子进程执行和重启恢复，并验证生产加载器从安装目录自动加载。测试插件位于 `tests/fixtures/agent_plugin`，使用确定性测试 CLI，不代表真实模型任务已验收。正式包仅包含 `loops_runtime`，不包含该测试插件。

支持自定义 Agent 工作目录的插件可接收可选 `write_paths=()`：平台仅在写模式的交付工作区不在 cwd 内时传入，用于保存声明产出。插件必须在原有权限机制内允许这些路径；只读执行不得启用写权限。旧插件不接受该参数时，平台明确拒绝需要此能力的执行，不静默放宽权限。
