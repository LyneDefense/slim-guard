class ToolRegistryError(Exception):
    pass


class DuplicateToolError(ToolRegistryError):
    pass


class UnknownToolError(ToolRegistryError):
    pass


class ToolGatewayConfigurationError(RuntimeError):
    pass


class ToolContextMismatchError(RuntimeError):
    pass


class ToolExecutionCollision(RuntimeError):
    pass


class ToolExecutionStateConflict(RuntimeError):
    pass
