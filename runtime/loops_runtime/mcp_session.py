"""Keep MCP output validation strict and report contract failures safely."""
from mcp import ClientSession


class ResultContractError(RuntimeError):
    """A safe category; provider validation errors can contain business data."""


class ValidatingClientSession(ClientSession):
    async def _validate_tool_result(self, name, result):
        try:
            await super()._validate_tool_result(name, result)
        except RuntimeError as error:
            raise ResultContractError('MCP 返回数据不符合服务声明的输出格式；结果未确认，请核对目标系统。') from error
