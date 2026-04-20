"""
Prompts管理模块
统一管理所有LLM提示词，支持模板化和多语言
"""

from typing import Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass


class PromptType(Enum):
    """提示词类型枚举"""
    SYSTEM = "system"
    USER = "user" 
    ASSISTANT = "assistant"


class AgentType(Enum):
    """Agent类型枚举"""
    USER_ANALYST = "user_analyst"
    CONTENT_STRATEGY = "content_strategy"
    CONTENT_GENERATOR = "content_generator"
    STRATEGY_COORDINATOR = "strategy_coordinator"
    TASK_ANALYZER = "task_analyzer"


@dataclass
class PromptTemplate:
    """提示词模板"""
    name: str
    agent_type: AgentType
    prompt_type: PromptType
    template: str
    description: str
    variables: list
    
    def format(self, **kwargs) -> str:
        """格式化提示词模板"""
        try:
            return self.template.format(**kwargs)
        except KeyError as e:
            missing_var = str(e).strip("'")
            raise ValueError(f"缺少必需的变量: {missing_var}。需要的变量: {self.variables}")

