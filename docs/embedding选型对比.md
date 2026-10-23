# Embedding 模型选型与部署（bge-m3 vs Qwen3-Embedding）

> 2026-10-23 · 实测对比结论

---

## 用户问题

"bge-m3 每次都很慢，是不是可以用 qwen3 试试，模型文件放哪了，放项目 data/models 合适吗"

## 实测对比（黄金集 v1 · dev 模式）

| 维度 | bge-m3 | Qwen3-Embedding-0.6B |
|---|---|---|
| 模型加载 | 9.1s | **4.5s（快 2 倍）** |
| 全量编码 401 条 FAQ | ~2-3 分钟 | **30s（快 5-10 倍）** |
| 检索质量 Recall@5 | **0.5（3/6 命中）** | 0.167（1/6 命中） |
| Precision@5 | **0.1** | 0.033 |
| 维度（schema 兼容） | 1024 ✅ | 1024 ✅（MRL） |
| 中文/英文双语 | ✅ | ✅（100+ 语言） |
| 部署 | 本地/API | 本地（trust_remote_code）/API |

**结论：速度 Qwen3 完胜，检索质量 bge-m3 完胜。**

对客服系统，**检索质量 > 编码速度**（速度可用"进程常驻"解决）。因此：
- **默认生产：bge-m3**（质量优先，客服答错比答慢代价高）
- **开发调试：可切 Qwen3**（`EMBEDDING_BACKEND=qwen3`，迭代快）
- **Qwen3 质量短板可调优**：instruction 前缀影响大，需针对 FAQ 场景调 task description 再比

## 慢的根因（换模型只解决一半）

1. **每次进程启动重新加载模型**（9s/4.5s）→ 根治：**embedding 服务化**（uvicorn 常驻，加载一次反复用）
2. **CPU 编码**（401 条 30s-3min）→ 可接受；数据量大再考虑 API 或 GPU
3. **HF 网络校验**（每次启动 ping huggingface.co）→ 已用 `HF_HUB_OFFLINE=1` 解决

## 模型文件位置

- **Qwen3-Embedding-0.6B**：项目内 `data/models/`（HF_HUB_CACHE 指向），随项目走，离线部署
- **bge-m3**：系统缓存 `~/.cache/huggingface/hub/`（4.3GB，避免重复占盘）
- `EmbeddingProvider._cache_dir()` 自动探测：`HF_HUB_CACHE` 环境变量 > 项目 `data/models` > 系统缓存

## 代码改动

- `app/providers/embedding_provider.py`：
  - 新增 `Qwen3LocalProvider`（transformers + trust_remote_code + instruction 检索格式）
  - `EmbeddingProvider.embed_query()` 默认方法（Qwen3 覆写加 instruction，上层不感知）
  - `_cache_dir()` 自动探测项目 data/models
- `app/eval_runner.py`：retrieve 走 `embed_query`（模型无关）
- `app/search.py`：防腐层驱动（原先硬编码 bge-m3）

## 切换方式

```bash
# bge-m3（默认，生产）
EMBEDDING_BACKEND=local

# Qwen3（开发调试）
EMBEDDING_BACKEND=qwen3

# API（未来，如 Qwen3-Embedding API）
EMBEDDING_BACKEND=api
```

## 模型缓存定位（修复后的行为）

- `EmbeddingProvider._cache_dir()` 自动探测：`HF_HUB_CACHE` 环境变量 > 项目 `data/models` > 系统缓存
- **bge-m3**：项目缓存无该模型时回退系统缓存（`_model_in_cache()` 判断），不强制项目缓存
- **Qwen3**：模型在项目 `data/models`，显式 cache_dir

## 实测环境备注

- 磁盘空间：Mac 磁盘满（124MB 可用），清理 `~/Library/Caches/pip`（3.7G）和 `pypoetry`（4.2G）后恢复 8.8G，Qwen3 才下载成功
- 若部署到服务器注意预留模型盘空间：bge-m3 4.3G + Qwen3-0.6B ~1.3G

## 待办（下一步）

1. **embedding 服务化**：uvicorn 常驻 /embed 接口，加载一次反复用（根治"每次慢"）
2. **Qwen3 instruction 调优**：换 FAQ 检索专用 task description 后再比一轮
3. **黄金集扩充**后重新对比两模型
