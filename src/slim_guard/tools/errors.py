class ToolRegistryError(Exception):
    pass


class DuplicateToolError(ToolRegistryError):
    pass


class UnknownToolError(ToolRegistryError):
    pass
