# 项目目录与模块说明

本文用**一棵目录树 + 行内注释**描述本仓库（磁盘上常见目录名为 `Legal_System`）的代码布局，便于一眼定位。  
说明：`__pycache__`、`node_modules`、`.pytest_cache`、`.venv` 等为运行生成物或本地环境，**不以下方树为必须提交内容**；树中只列仓库内应存在的源码与配置路径。

```text
Legal_System/                        # 本仓库根目录
├── .env                              # 本地/部署实际配置（勿提交公共仓库）
├── .env.example                      # 环境变量模板（复制为 .env 后填写）
├── pyproject.toml                    # 既是项目工具说明书也是依赖文件、pytest/ruff；testpaths = offline/tests（当运行pytest时，通过pyproject.toml找到对应测试目录及数据等，开发时可选用，生产不用，如果是装依赖的话requirements.txt就够 ）
├── requirements.txt                  # pip 运行时依赖
├── requirements-dev.txt              # 需要ai在本地改代码，像仓库管理维护者一样跑测试、检查格式（pytest(自动测试代码对不对的工具)、ruff(检查代码风格和常见问题的工具、保持代码干净) 等）
├── uv.lock                           # 与 pyproject 配套的 uv 锁定文件（如果有人用uv管理依赖，会用该文件锁定每一个包的依赖版本，其他人执行uv  sync就能复现同一套环境，本项目用conda环境所以不用管）
├── ARCHITECTURE.md                   # 本文：架构与目录说明
├── 启动与部署.md                    # 启动顺序、依赖服务与运维入口
│
├── backend/                          # HTTP API 层（FastAPI），与领域逻辑解耦
│   └── app/
│       ├── main.py                   # FastAPI 应用：CORS、/health、挂载 /api
│       ├── lifespan.py               # 启动：异步建表、Milvus ensure；可选后台循环 MySQL→Milvus 全量同步
│       ├── deps.py                   # get_pipeline：RagPipeline 进程内单例
│       ├── schemas.py                # ChatRequest（含可选 user_external_id）、HealthResponse
│       └── api/
│           └── chat.py              # POST /api/chat/stream（SSE）；结束后 persist_user_turn
│
├── modules/                          # 领域模块（可被 offline 脚本与 backend 共用）
│   ├── core/
│   │   └── config.py                 # 全局 Settings（.env）；阈值、模型路径、Milvus/Redis 等
│   ├── database/
│   │   ├── models.py                 # ORM：faq_tab、legal_tab、users_tab、his_chat_tab 等
│   │   └── session.py                # 异步引擎与会话工厂
│   ├── cache/
│   │   └── redis_client.py           # Redis 异步客户端；问答 JSON 缓存（key 可含 user_external_id）
│   ├── embeddings/
│   │   └── local_embedding.py        # 本地 BGE-M3 句向量
│   ├── rerank/
│   │   └── local_rerank.py           # 本地 Cross-Encoder 重排
│   ├── milvus_store/
│   │   ├── client.py                 # PyMilvus 连接
│   │   └── collections.py            # 集合 schema、创建/确保集合存在
│   ├── ingestion/                    # 离线数据管道（业务实现；入口在 offline/scripts）
│   │   ├── mysql_loaders.py          # Excel/PDF → MySQL（faq_tab / legal_tab）
│   │   ├── chunking.py               # 法律文本父子分块
│   │   ├── pdf_extract.py            # PDF 抽取
│   │   └── milvus_sync.py            # MySQL → Milvus 全量同步
│   ├── memory/
│   │   ├── history_detect.py         # 「自述聊天历史」类问题正则
│   │   └── service.py                # 解析用户、读最近 10 条 his_chat、格式化注入；持久化一轮对话
│   └── rag/
│       ├── intent.py                 # 意图与路由（FAQ / 法律等）
│       ├── hybrid_rrf.py             # 混合检索与 RRF 融合
│       ├── dashscope_http.py         # 通义（DashScope）HTTP 流式调用
│       ├── prompts.py                # 系统提示与用户消息拼装
│       └── pipeline.py               # 端到端 RAG：缓存、记忆、检索、重排、生成
│
├── offline/                          # 离线流水线入口脚本与 pytest（业务逻辑在 modules/ingestion）
│   ├── scripts/
│   │   ├── run_mysql_ingest.py       # 薄封装：调用默认文件入库（MySQL）
│   │   ├── run_milvus_sync.py        # 薄封装：全量写 Milvus
│   │   └── run_full_offline.py       # 入库 + Milvus 同步一键
│   └── tests/                        # pytest 收集路径（见 pyproject.toml testpaths）
│       ├── test_chunking.py          # 分块逻辑单测（无真实 DB）
│       └── test_history_detect.py    # 记忆意图正则单测
│
├── deploy/
│   ├── docker-compose.yml            # Redis、etcd、MinIO、Milvus 等依赖与卷
│   ├── docker-daemon-cn.json         # Docker 镜像加速配置片段（可选合并进 Docker Engine）
│   └── milvus-deps.tar（可选）       # 离线 `docker load` 用镜像包；体积大，按需自备/生成，通常勿提交公仓
│
├── data/                             # 默认数据源：FAQ Excel、法律 PDF（入库脚本读取；大文件勿提交公仓）
├── models/                           # 本地 Embedding / Rerank 权重（如 bge-m3、bge-reranker-large；体积大）
│
├── scripts/
│   └── init_mysql.sql                # 建库 xiaoyi_rag（utf8mb4）；业务表由应用 create_all
│
└── frontend/                         # Vue 3 + Vite 对话前端（SSE）
    ├── index.html                    # 挂载 #app
    ├── package.json                  # 依赖与脚本
    ├── package-lock.json
    ├── vite.config.js                # 开发端口、/api 代理到后端 8000
    └── src/
        ├── main.js                   # createApp、全局 styles
        ├── App.vue                   # 对话 UI、fetch SSE；user_external_id（localStorage UUID）
        └── styles.css                # 主题与页面样式
```

