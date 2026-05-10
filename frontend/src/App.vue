<!--
  对话页根组件：顶部标题区 + 消息列表（用户/助手）+ 输入框；通过 fetch 读 SSE 流式拼接助手回复。
  请求体携带 user_external_id（localStorage 持久化 UUID），供后端 users_tab / his_chat_tab 记忆功能；
  详见仓库根目录「启动与部署.md」第 7 节。
-->
<script setup>
// Vue 3 编译宏：<script setup> 顶层绑定自动暴露给模板，无需 export default
import { nextTick, ref } from "vue";

const input = ref(""); // 输入框双向绑定内容
const messages = ref([
  {
    role: "assistant",
    text: "你好，我是智能小易。我可以结合税法、劳动法知识库为你解答。试试问我「经济补偿金如何计算？」",
  },
]); // 对话历史：每项含 role / text，助手流式时可带 streaming 标记
const loading = ref(false); // 发送中禁用按钮防重复提交
const listRef = ref(null); // 消息列表容器，用于滚动到底

// 与后端 ChatRequest.user_external_id 对应；同一浏览器复用同一 id，清空 localStorage 即视为新用户
const USER_KEY = "xiaoyi_user_external_id";
function getOrCreateUserExternalId() {
  let id = localStorage.getItem(USER_KEY);
  if (!id) {
    id = crypto.randomUUID(); // Web Crypto：无需额外依赖
    localStorage.setItem(USER_KEY, id);
  }
  return id;
}

async function scrollToBottom() {
  await nextTick(); // 等待 DOM 更新后再读 scrollHeight
  const el = listRef.value;
  if (el) el.scrollTop = el.scrollHeight;
}

async function send() {
  const q = input.value.trim();
  if (!q || loading.value) return; // 空内容或加载中不发请求
  messages.value.push({ role: "user", text: q });
  input.value = "";
  loading.value = true;
  messages.value.push({ role: "assistant", text: "", streaming: true }); // 预占位以便流式追加
  await scrollToBottom();

  const idx = messages.value.length - 1; // 当前助手气泡索引
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: q, user_external_id: getOrCreateUserExternalId() }),
    });
    if (!res.ok || !res.body) {
      messages.value[idx].text = `请求失败：${res.status}`;
      messages.value[idx].streaming = false;
      loading.value = false;
      return;
    }
    const reader = res.body.getReader(); // ReadableStream 读取器
    const decoder = new TextDecoder();
    let buf = ""; // 半行缓冲（SSE 可能被 TCP 拆包）
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() || "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6).trim();
        if (payload === "[DONE]") continue;
        try {
          const obj = JSON.parse(payload);
          if (obj.chunk) {
            messages.value[idx].text += obj.chunk;
            await scrollToBottom();
          }
          if (obj.error) {
            messages.value[idx].text += `\n[错误] ${obj.error}`;
          }
        } catch {
          /* 忽略不完整 JSON 片段 */
        }
      }
    }
  } catch (e) {
    messages.value[idx].text = `网络错误：${e}`;
  } finally {
    messages.value[idx].streaming = false;
    loading.value = false;
    await scrollToBottom();
  }
}

function onKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault(); // 阻止默认换行，改为发送
    send();
  }
}
</script>

<template>
  <div class="shell">
    <header class="hero">
      <h1>智能小易</h1>
    </header>

    <main class="panel">
      <!-- 消息区：max-height 限制 + 内部滚动 -->
      <div ref="listRef" class="messages">
        <div
          v-for="(m, i) in messages"
          :key="i"
          class="row"
          :class="m.role"
        >
          <div class="avatar">{{ m.role === "user" ? "你" : "易" }}</div>
          <div class="bubble">
            <span v-if="m.streaming && !m.text" class="typing">智能小易正在思考…</span>
            <div class="text">{{ m.text }}</div>
          </div>
        </div>
      </div>

      <div class="composer">
        <textarea
          v-model="input"
          rows="2"
          placeholder="输入你的问题，Enter 发送，Shift+Enter 换行"
          @keydown="onKey"
        />
        <button type="button" :disabled="loading" @click="send">
          {{ loading ? "生成中…" : "发送" }}
        </button>
      </div>
    </main>
  </div>
</template>

<style scoped>
/* 页面外壳：居中窄栏 + 纵向 flex */
.shell {
  max-width: 880px;
  margin: 0 auto;
  padding: 2.5rem 1.25rem 4rem;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.hero {
  text-align: center;
}

h1 {
  margin: 0;
  font-size: 2.25rem;
  font-weight: 600;
  background: linear-gradient(120deg, #5eead4, #a5b4fc);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: rgba(15, 23, 42, 0.55);
  border: 1px solid rgba(148, 163, 200, 0.2);
  border-radius: 22px;
  padding: 1rem;
  backdrop-filter: blur(12px);
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem;
  max-height: min(58vh, 640px);
  scroll-behavior: smooth;
}

.row {
  display: flex;
  gap: 0.75rem;
  margin-bottom: 1rem;
  align-items: flex-start;
}

.row.user {
  flex-direction: row-reverse;
}

.avatar {
  width: 36px;
  height: 36px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  font-size: 0.85rem;
  font-weight: 600;
  flex-shrink: 0;
  background: rgba(148, 163, 200, 0.15);
  border: 1px solid rgba(148, 163, 200, 0.25);
}

.row.user .avatar {
  background: var(--bubble-user);
  border-color: rgba(94, 234, 212, 0.35);
}

.row.assistant .avatar {
  background: var(--bubble-bot);
  border-color: rgba(129, 140, 248, 0.4);
}

.bubble {
  max-width: 78%;
  padding: 0.85rem 1rem;
  border-radius: var(--radius);
  line-height: 1.65;
  font-size: 0.95rem;
  border: 1px solid rgba(148, 163, 200, 0.18);
}

.row.user .bubble {
  background: var(--bubble-user);
  border-color: rgba(94, 234, 212, 0.25);
}

.row.assistant .bubble {
  background: rgba(15, 23, 42, 0.65);
}

.text {
  white-space: pre-wrap;
  word-break: break-word;
}

.typing {
  color: var(--muted);
  font-size: 0.9rem;
}

.composer {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.75rem;
  margin-top: 0.75rem;
  padding-top: 0.75rem;
  border-top: 1px solid rgba(148, 163, 200, 0.15);
}

textarea {
  width: 100%;
  resize: none;
  border-radius: 14px;
  border: 1px solid rgba(148, 163, 200, 0.25);
  background: rgba(15, 23, 42, 0.7);
  color: var(--text);
  padding: 0.75rem 1rem;
  font: inherit;
  outline: none;
}

textarea:focus {
  border-color: rgba(94, 234, 212, 0.45);
  box-shadow: 0 0 0 3px rgba(94, 234, 212, 0.12);
}

button {
  border: none;
  border-radius: 14px;
  padding: 0 1.35rem;
  font-weight: 600;
  cursor: pointer;
  color: #0f172a;
  background: linear-gradient(130deg, #5eead4, #818cf8);
  box-shadow: 0 10px 30px rgba(94, 234, 212, 0.2);
  transition: transform 0.12s ease, opacity 0.12s ease;
}

button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

button:not(:disabled):hover {
  transform: translateY(-1px);
}

</style>
