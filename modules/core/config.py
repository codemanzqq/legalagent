"""
全局配置模块：使用 pydantic-settings 从环境变量与 `.env` 文件加载参数。

同一进程内通过 `lru_cache` 缓存 `Settings` 实例，避免重复解析磁盘。
"""

from functools import lru_cache  # 将 get_settings 缓存为单例

from pydantic import Field, field_validator  # 字段定义与校验装饰器
from pydantic_settings import BaseSettings, SettingsConfigDict  # 配置基类与模型配置


class Settings(BaseSettings):
    """应用配置：字段 `alias` 与 `.env` 中大写键名一致。"""

    model_config = SettingsConfigDict(
        env_file=".env",  # 默认从项目根目录（工作目录）读取
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略 .env 中未声明的多余项，避免第三方库变量导致报错
    )

    # ----- DashScope（阿里云 OpenAI 兼容接口）-----
    dashscope_api_key: str = Field(default="", alias="DASHSCOPE_API_KEY")  # 为空时意图步骤会跳过 API
    dashscope_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="DASHSCOPE_BASE_URL",
    )
    llm_model: str = Field(default="qwen-max", alias="LLM_MODEL")  # 主生成模型
    intent_model: str = Field(default="qwen-turbo", alias="INTENT_MODEL")  # 意图分类用小模型降低成本

    # ----- MySQL（例如小皮面板安装的本地实例）-----
    mysql_host: str = Field(default="127.0.0.1", alias="MYSQL_HOST")
    mysql_port: int = Field(default=3306, alias="MYSQL_PORT")
    mysql_user: str = Field(default="root", alias="MYSQL_USER")
    mysql_password: str = Field(default="", alias="MYSQL_PASSWORD")
    mysql_database: str = Field(default="xiaoyi_rag", alias="MYSQL_DATABASE")

    # ----- Redis（问答缓存）-----
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", alias="REDIS_URL")
    cache_ttl_seconds: int = Field(default=3600, alias="CACHE_TTL_SECONDS")  # 缓存过期秒数

    # ----- Milvus（向量数据库，常为 Docker 暴露端口）-----
    milvus_host: str = Field(default="127.0.0.1", alias="MILVUS_HOST")
    milvus_port: int = Field(default=19530, alias="MILVUS_PORT")
    milvus_user: str = Field(default="", alias="MILVUS_USER")  # 未启用鉴权时留空
    milvus_password: str = Field(default="", alias="MILVUS_PASSWORD")

    # ----- 本地 Transformer 模型路径（相对仓库根目录）-----
    embedding_model_path: str = Field(default="models/bge-m3", alias="EMBEDDING_MODEL_PATH")
    rerank_model_path: str = Field(
        default="models/bge-reranker-large",
        alias="RERANK_MODEL_PATH",
    )
    embedding_device: str = Field(default="cpu", alias="EMBEDDING_DEVICE")  # 无 GPU 时 cpu

    # ----- 热更新：定时把 MySQL 全量刷到 Milvus -----
    hot_update_enabled: bool = Field(default=True, alias="HOT_UPDATE_ENABLED")
    hot_update_interval_seconds: int = Field(default=0, alias="HOT_UPDATE_INTERVAL_SECONDS")  # <=0 时用 effective 回退

    # ----- 法律混合检索规模参数 -----
    legal_hybrid_bm25_enabled: bool = Field(default=True, alias="LEGAL_HYBRID_BM25_ENABLED")
    hybrid_dense_candidate_k: int = Field(default=60, alias="HYBRID_DENSE_CANDIDATE_K")  # 向量候选条数
    hybrid_bm25_candidate_k: int = Field(default=60, alias="HYBRID_BM25_CANDIDATE_K")  # BM25 参与 RRF 的截断
    hybrid_rrf_k: int = Field(default=60, alias="HYBRID_RRF_K")  # RRF 公式平滑系数 k

    # ----- FAQ 阈值：字段名历史原因含 distance，实为「与完全匹配的偏差」；COSINE 命中分数为相似度 -----
    faq_direct_distance_threshold: float = Field(default=0.01, alias="FAQ_DIRECT_DIST_THRESH")  # 直达：sim >= 1-0.01
    faq_llm_distance_threshold: float = Field(default=0.15, alias="FAQ_LLM_DIST_THRESH")  # 拼上下文：sim >= 1-0.15
    faq_top_k_for_llm: int = Field(default=3, alias="FAQ_TOP_K_FOR_LLM")  # 最多几条 FAQ 参考
    legal_rerank_top_n: int = Field(default=5, alias="LEGAL_RERANK_TOP_N")  # 精排后送入 LLM 的父文档数

    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )  # 逗号分隔，浏览器来源白名单

    @property
    def mysql_dsn_async(self) -> str:
        """构造 SQLAlchemy 异步驱动 URL（aiomysql）。"""
        return (
            f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )

    @property
    def effective_hot_update_interval_seconds(self) -> int:
        """热更新间隔：配置 <=0 时回退 60 秒，避免忙等。"""
        if self.hot_update_interval_seconds <= 0:
            return 60
        return self.hot_update_interval_seconds

    def cors_origin_list(self) -> list[str]:
        """将逗号分隔字符串解析为列表供 CORSMiddleware 使用。"""
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

    @field_validator("faq_direct_distance_threshold", "faq_llm_distance_threshold")
    @classmethod
    def positive_thresh(cls, v: float) -> float:
        """阈值不允许为负。"""
        if v < 0:
            raise ValueError("threshold must be non-negative")
        return v


@lru_cache
def get_settings() -> Settings:
    """供全项目调用的配置单例工厂。"""
    return Settings()


ASSISTANT_NAME = "小易"  # 助手昵称：Prompt 与前端展示共用
