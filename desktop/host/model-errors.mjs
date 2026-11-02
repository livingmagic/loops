export function modelFailureMessage(error) {
  const status = Number(error?.status || String(error?.message || '').match(/^(\d{3}):/)?.[1]);
  const reason = status === 503 ? 'DeepSeek 服务繁忙（503），本次请求未完成，请稍后重试。'
    : status === 429 ? 'DeepSeek 请求频率受限（429），请稍后重试。'
    : status === 401 ? 'DeepSeek 身份验证失败（401），请检查本机密钥配置。'
    : status === 402 ? 'DeepSeek 账户余额不足（402），请检查账户额度。'
    : '自动化 AI 请求未完成，请检查本机模型配置和网络。';
  return reason + '对话和已完成的修改已保留。';
}
