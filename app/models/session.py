"""
Session models for state persistence
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class SessionStage(str, Enum):
    INIT = "init"
    STRATEGY = "strategy"
    GENERATION = "generation"
    COMPLETED = "completed"
    FAILED = "failed"


class SessionLifecycleState(str, Enum):
    ALIVE = "alive"
    FROZEN = "frozen"
    PURGED = "purged"


class SessionError(BaseModel):
    """错误记录"""
    code: str
    message: str
    stage: SessionStage
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class SpiderNote(BaseModel):
    """精简后的 Spider 笔记 - 仅用于相似度检查"""
    note_id: str
    title: str
    content: str
    tags: List[str]


class ContentStrategy(BaseModel):
    """内容策略（从 Strategy Agent 产出）"""
    positioning: str
    target_audience: str
    content_pillars: List[str]
    key_messaging: str
    content_types: List[str]
    posting_strategy: str
    data_source_quality: float  # quality_score


class PlatformPreference(BaseModel):
    """平台偏好分析"""
    avg_title_length: int
    popular_tags: List[str]
    optimal_posting_times: List[str]
    content_patterns: List[str]


class Proposal(BaseModel):
    """完整内容提案"""
    proposal_id: str
    angle: str
    hook: str
    outline: str
    target_emotion: str
    content_pillars: List[str]
    suggested_tags: List[str]
    score: float = 0.0
    is_used: bool = False  # 是否被选中使用
    is_high_risk: bool = False  # 是否被标记为高风险（相似度过高）


class GenerationAttempt(BaseModel):
    """单次生成尝试记录（用于追溯和统计）"""
    attempt_id: str
    proposal_id: str
    temperature: float
    generated_title: str
    similarity_score: float
    status: str  # "success", "high_similarity_retry", "failed"
    retry_count: int = 0
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class GeneratedNote(BaseModel):
    """生成的笔记"""
    note_id: str
    title: str
    content: str
    tags: List[str]
    cover_design_prompt: str
    suggested_update_time: str
    similarity_check: Dict[str, Any]  # {max_similarity: float, status: str}
    generation_params: Dict[str, Any]  # {temperature: float, proposal_id: str}


class RetryStats(BaseModel):
    """重试统计"""
    query_expansion_count: int = 0  # query expansion 重试次数
    spider_attempt_count: int = 0  # spider 调用尝试次数（最多5次）
    generation_retry_count: int = 0  # generation 阶段重试次数（proposal 重选）


class Session(BaseModel):
    """
    完整 Session 模型 - 存储在 SQLite 的 JSON 字段中
    """
    # === 基本信息 ===
    session_id: str
    user_id: str
    user_query: str
    platform: str = "xiaohongshu"
    mode: str = "editing"
    stage: SessionStage = SessionStage.INIT
    lifecycle_state: SessionLifecycleState = SessionLifecycleState.ALIVE
    alive_until: Optional[datetime] = None
    pause_requested: bool = False
    pause_requested_at: Optional[datetime] = None
    spider_cooldown_until: Optional[datetime] = None
    purge_after: Optional[datetime] = None
    frozen_at: Optional[datetime] = None
    purged_at: Optional[datetime] = None
    
    # === Strategy 阶段数据 ===
    spider_notes: Optional[List[SpiderNote]] = None  # 精简后的笔记（仅相似度检查用）
    quality_score: Optional[float] = None  # 0-1
    platform_preference: Optional[PlatformPreference] = None
    content_strategy: Optional[ContentStrategy] = None
    expanded_queries: Optional[List[str]] = None  # 扩展查询记录
    used_fallback: bool = False  # 是否使用了 generic 策略
    
    # === Generation 阶段数据 ===
    proposals: Optional[List[Proposal]] = None  # 10个完整提案
    selected_proposal_ids: Optional[List[str]] = None  # 选中的 top-5 proposal ids
    generated_notes: Optional[List[GeneratedNote]] = None  # 最终结果
    generation_attempts: Optional[List[GenerationAttempt]] = None  # 所有尝试记录
    similarity_report: Optional[Dict[str, Any]] = None  # 相似度统计
    
    # === 精简 State 引用 ===
    spider_note_ids: Optional[List[str]] = None
    strategy_id: Optional[str] = None
    proposal_ids: Optional[List[str]] = None
    generated_note_ids: Optional[List[str]] = None
    
    # === 重试统计 ===
    retry_stats: RetryStats = Field(default_factory=RetryStats)

    # === 补偿状态 ===
    reindex_state: str = "ok"
    reindex_attempts: int = 0
    
    # === 错误和元数据 ===
    error: Optional[SessionError] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity_at: datetime = Field(default_factory=datetime.utcnow)
    last_user_activity_at: datetime = Field(default_factory=datetime.utcnow)
