"""
统一异常类型定义。

所有 AI Memory Hub 的自定义异常都应该从这个模块导出。
"""


class MemoryHubError(Exception):
    """基础异常类，所有项目特定异常的基类。"""
    pass


class StoreError(MemoryHubError):
    """存储相关错误，如数据库连接失败、文件操作失败等。"""
    pass


class ValidationError(MemoryHubError):
    """数据验证错误，如缺少必填字段、字段类型不匹配等。"""
    pass


class ConfigurationError(MemoryHubError):
    """配置错误，如配置文件缺失、格式错误等。"""
    pass


class SearchError(MemoryHubError):
    """搜索相关错误，如 FTS 查询失败、向量存储错误等。"""
    pass


class LLMError(MemoryHubError):
    """LLM 调用相关错误，如 API 调用失败、响应解析错误等。"""
    pass
