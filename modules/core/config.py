# =============================================================================
# 教学说明：本文件在整体链路中的位置
# -----------------------------------------------------------------------------
# 输入：进程环境变量、项目根目录下的 `.env` 文件（键名与下方 Field 的 alias 一致）。
# 输出：`Settings` 数据类实例（字段已校验、类型已转换）；`get_settings()` 返回进程内单例。
# 被谁调用：几乎全项目——`session.py`（MySQL DSN）、`redis_client`、`milvus`、
#          `RagPipeline`、`ChatOpenAI` 构造、CORS、`lifespan` 热更新间隔等。
# =============================================================================
"""
本模块用 pydantic-settings 把「.env + 环境变量」变成类型安全的 Python 对象。

技巧：`@lru_cache` 包住 `get_settings()`，保证全进程只解析一次磁盘，避免每次请求都读文件。
"""

from functools import lru_cache  # 装饰器：把「无参函数」的返回值缓存起来，重复调用直接返回旧结果

from pydantic import Field, field_validator  # Field：声明字段默认值与 env 别名；field_validator：自定义校验
from pydantic_settings import BaseSettings, SettingsConfigDict  # BaseSettings：可自动从环境变量填充；SettingsConfigDict：模型级配置


class Settings(BaseSettings):
    """
    一条配置 = 一个类属性；`alias` 必须与 `.env` 里的大写变量名一致，pydantic 才会自动映射。
    """

    model_config = SettingsConfigDict(
        env_file=".env",  # 告诉 pydantic：启动时尝试从「当前工作目录」下的 .env 读入变量
        env_file_encoding="utf-8",  # .env 文件按 UTF-8 解码，避免中文路径或注释乱码
        extra="ignore",  # .env 里若有多余键（例如你只临时 export 了别的变量），不报错直接忽略
    )

    # ----- DashScope（阿里云 OpenAI 兼容接口）：意图模型 + 主生成模型 -----
    dashscope_api_key: str = Field(default="", alias="DASHSCOPE_API_KEY")  # 空字符串表示未配置：意图模块会降级跳过 API
    dashscope_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="DASHSCOPE_BASE_URL",
    )  # LangChain ChatOpenAI 的 base_url，指向 DashScope 兼容 OpenAI SDK 的入口
    llm_model: str = Field(default="qwen-max", alias="LLM_MODEL")  # 流式回答用的大模型名
    intent_model: str = Field(default="qwen-turbo", alias="INTENT_MODEL")  # 只做 true/false 分类，用小模型省成本

    # ----- MySQL：异步驱动 aiomysql 的连接四要素 -----
    mysql_host: str = Field(default="127.0.0.1", alias="MYSQL_HOST")  # 数据库主机，本机常用 127.0.0.1
    mysql_port: int = Field(default=3306, alias="MYSQL_PORT")  # 端口，MySQL 默认 3306
    mysql_user: str = Field(default="root", alias="MYSQL_USER")  # 登录用户名
    mysql_password: str = Field(default="", alias="MYSQL_PASSWORD")  # 登录密码；空串表示无密码（仅开发环境）
    mysql_database: str = Field(default="xiaoyi_rag", alias="MYSQL_DATABASE")  # 库名，与建库 SQL 一致

    # ----- Redis：字符串 URL，内含库号 / 密码（若有）-----
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", alias="REDIS_URL")  # 默认本机 6379，数据库编号 0
    cache_ttl_seconds: int = Field(default=3600, alias="CACHE_TTL_SECONDS")  # 问答缓存过期时间，单位秒

    # ----- Milvus：向量库 gRPC 地址 -----
    milvus_host: str = Field(default="127.0.0.1", alias="MILVUS_HOST")  # Docker 映射到本机时常为 127.0.0.1
    milvus_port: int = Field(default=19530, alias="MILVUS_PORT")  # Milvus 默认监听端口
    milvus_user: str = Field(default="", alias="MILVUS_USER")  # 单机无鉴权时留空字符串
    milvus_password: str = Field(default="", alias="MILVUS_PASSWORD")  # 同上，空表示不传密码给 pymilvus

    # ----- 本地 Transformer 权重目录（相对「仓库根」的路径字符串）-----
    embedding_model_path: str = Field(default="models/bge-m3", alias="EMBEDDING_MODEL_PATH")  # BGE-M3 句向量模型文件夹
    rerank_model_path: str = Field(
        default="models/bge-reranker-large",
        alias="RERANK_MODEL_PATH",
    )  # CrossEncoder 重排模型文件夹
    embedding_device: str = Field(default="cpu", alias="EMBEDDING_DEVICE")  # 推理设备：cpu 或 cuda:0 等

    # ----- 热更新：定时把 MySQL 全量刷到 Milvus（简化版一致性）-----
    hot_update_enabled: bool = Field(default=True, alias="HOT_UPDATE_ENABLED")  # True：lifespan 里起后台任务
    hot_update_interval_seconds: int = Field(default=0, alias="HOT_UPDATE_INTERVAL_SECONDS")  # <=0 时由 effective_* 回退为 60

    # ----- 法律混合检索：稠密 + BM25 + RRF 的规模参数 -----
    legal_hybrid_bm25_enabled: bool = Field(default=True, alias="LEGAL_HYBRID_BM25_ENABLED")  # False 则只做向量一路排序
    hybrid_dense_candidate_k: int = Field(default=60, alias="HYBRID_DENSE_CANDIDATE_K")  # Milvus 向量检索先取 Top-K 子块
    hybrid_bm25_candidate_k: int = Field(default=60, alias="HYBRID_BM25_CANDIDATE_K")  # BM25 排序后截断，再与稠密路做 RRF
    hybrid_rrf_k: int = Field(default=60, alias="HYBRID_RRF_K")  # RRF 公式里的 k--RRF 衰减系数，越大高名次衰减越慢

    # ----- FAQ：COSINE 相似度与「距离阈值」的换算在 pipeline 里完成 -----
    faq_direct_distance_threshold: float = Field(default=0.01, alias="FAQ_DIRECT_DIST_THRESH")  # 直达答案：相似度 >= 1-该值
    faq_llm_distance_threshold: float = Field(default=0.15, alias="FAQ_LLM_DIST_THRESH")  # 拼进 LLM：相似度 >= 1-该值
    faq_top_k_for_llm: int = Field(default=3, alias="FAQ_TOP_K_FOR_LLM")  # 最多几条 FAQ 片段进上下文
    legal_rerank_top_n: int = Field(default=5, alias="LEGAL_RERANK_TOP_N")  # 父文档重排后取前 N 篇全文进 LLM

    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )  # 浏览器 Origin 白名单，逗号分隔；默认允许本地开发的前端（5173 端口）调用接口，避免跨域报错

    @property
    def mysql_dsn_async(self) -> str:
        """
        SQLAlchemy 异步 URL：协议头必须是 mysql+aiomysql，后面跟用户名密码主机库名。

        入参:
            无（读取当前 `Settings` 实例字段）。
        返回:
            `mysql+aiomysql://...` 形式的异步数据库连接 URL 字符串。
        """
        return (
            f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"  # 用户名密码中的特殊字符需 URL 编码（此处未编码，密码勿含 @ 等）
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"  # 主机:端口/数据库名
        )

    @property
    def effective_hot_update_interval_seconds(self) -> int:
        """
        若用户把间隔配成 0 或负数，这里统一回退 60 秒，避免 while 忙等打满 CPU。

        入参:
            无。
        返回:
            实际使用的热更新间隔秒数（正整数）。
        """
        if self.hot_update_interval_seconds <= 0:  # 非法或非正间隔
            return 60  # 安全默认值：每分钟最多全量同步一次
        return self.hot_update_interval_seconds  # 否则尊重用户配置

    def cors_origin_list(self) -> list[str]:
        """
        CORSMiddleware 需要 Python 列表；把逗号分隔字符串拆成去空格后的列表。

        入参:
            无。
        返回:
            非空的 Origin 字符串列表，供 CORS 白名单使用。
        """
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]  # 过滤空段，避免白名单里出现 ""

    #这是 Pydantic v2 的写法，在 Settings 从环境变量/.env 填好字段之后、正式生效之前，对指定字段再做一道检查。
    @field_validator("faq_direct_distance_threshold", "faq_llm_distance_threshold") #这两个字段的值都会交给下面的函数校验：不允许负数，否则相似度换算会乱。
    @classmethod
    def positive_thresh(cls, v: float) -> float:
        """
        阈值在业务上表示「与完全匹配的偏差」，不允许负数，否则相似度换算会乱。

        入参:
            cls: Pydantic 校验器约定的类对象。
            v: 待校验的阈值原始浮点值。
        返回:
            校验通过后的同一浮点值；若 v<0 则抛出 ValueError。
        """
        if v < 0:  # 负数无物理意义
            raise ValueError("threshold must be non-negative")  # 启动时直接失败，强迫修正 .env
        return v  # 校验通过原样返回


@lru_cache
def get_settings() -> Settings:
    """
    全项目统一入口：第一次调用时构造 Settings()，之后永远返回同一对象实例。

    入参:
        无。
    返回:
        进程内缓存的 `Settings` 单例。
    """
    return Settings()  # 触发 pydantic 从环境变量 + .env 填充字段


ASSISTANT_NAME = "小易"  # 常量：prompts 与兜底文案里引用，修改一处即可改助手昵称
