<!--
=============================================================================
教学说明：本文件在整体链路中的位置
-----------------------------------------------------------------------------
输入：用户在 textarea 中的文本；localStorage 中键 xiaoyi_user_external_id（若无则生成 UUID）。
输出：浏览器内 messages 列表更新；向 POST /api/chat/stream 发送 JSON；解析 SSE 增量更新助手气泡。
被谁调用：浏览器加载前端入口后由 Vite 挂载本组件；不经过 Python，仅通过 HTTP 与 FastAPI 通信。
与后端契约：请求体 { message, user_external_id }；响应 text/event-stream，每行 data: {...}。
=============================================================================
对话页根组件：消息列表 + 输入框；fetch 流式读 SSE，把 chunk 拼到当前助手消息上。
-->
<script setup>
// Vue 3 编译宏：script setup 顶层变量/函数自动暴露给模板，无需 export default
import { nextTick, ref } from "vue"; // nextTick：DOM 更新后再滚动；ref：响应式包装基本类型/对象

const input = ref(""); // 输入框绑定值；初始空字符串
const messages = ref([
  {
    role: "assistant",
    text: "你好，我是智能小易。我可以结合税法、劳动法知识库为你解答。试试问我「经济补偿金如何计算？」",
  },
]); // 初始一条欢迎语；后续 push 用户/助手消息
const loading = ref(false); // true 时禁用发送按钮，防连点
const listRef = ref(null); // 绑定模板里消息列表容器的 DOM 引用，用于 scrollTop

// 与后端 ChatRequest.user_external_id 一致：同一浏览器固定一个 UUID，清空 localStorage 即新用户
const USER_KEY = "xiaoyi_user_external_id";
function getOrCreateUserExternalId() {
  let id = localStorage.getItem(USER_KEY); // 尝试读取已有 id
  if (!id) {
    id = crypto.randomUUID(); // 标准 Web API 生成 UUID v4
    localStorage.setItem(USER_KEY, id); // 持久化到浏览器本地存储
  }
  return id; // 每次请求带上，后端据此关联 users_tab
}

async function scrollToBottom() {
  await nextTick(); // 等待 Vue 把新消息渲染进 DOM
  const el = listRef.value; // 取 div.messages 元素
  if (el) el.scrollTop = el.scrollHeight; // 滚动条置底，显示最新消息
}

async function send() {
  const q = input.value.trim(); // 去掉首尾空白
  if (!q || loading.value) return; // 空问题或加载中：不发请求
  messages.value.push({ role: "user", text: q }); // 用户气泡立即出现
  input.value = ""; // 清空输入框
  loading.value = true; // 进入加载态
  messages.value.push({ role: "assistant", text: "", streaming: true }); // 先占位一条空助手消息，streaming 用于显示「思考中」
  await scrollToBottom();

  const idx = messages.value.length - 1; // 刚追加的助手消息下标
  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: q, user_external_id: getOrCreateUserExternalId() }),
    }); // Vite dev 会把 /api 代理到后端，见 vite.config.js
    if (!res.ok || !res.body) {
      // HTTP 4xx/5xx 或浏览器不支持 body 流
      messages.value[idx].text = `请求失败：${res.status}`;
      messages.value[idx].streaming = false;
      loading.value = false;
      return;
    }
    const reader = res.body.getReader(); // 取得 ReadableStreamDefaultReader
    const decoder = new TextDecoder(); // UTF-8 字节解码为字符串
    let buf = ""; // 累积半行：SSE 可能把一行拆成多次 read
    while (true) {
      const { done, value } = await reader.read(); // 读下一块 Uint8Array
      if (done) break; // 流结束
      buf += decoder.decode(value, { stream: true }); // stream:true 表示后续还有字节，避免多字节字符截断
      const lines = buf.split("\n"); // 按换行切
      buf = lines.pop() || ""; // 最后一段可能不完整，留到下次与后续字节拼接
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue; // 忽略 SSE 里非 data 行（如空行）
        const payload = line.slice(6).trim(); // 去掉前缀 "data: "
        if (payload === "[DONE]") continue; // 结束标记，无需 JSON 解析
        try {
          const obj = JSON.parse(payload); // 后端 json.dumps 的对象
          if (obj.chunk) {
            messages.value[idx].text += obj.chunk; // 拼接到助手气泡
            await scrollToBottom(); // 每块更新后跟随滚动
          }
          if (obj.error) {
            messages.value[idx].text += `\n[错误] ${obj.error}`; // 后端在流中返回的错误对象
          }
        } catch {
          /* JSON 被 TCP 截断时不完整，忽略本次等下一行 */
        }
      }
    }
  } catch (e) {
    messages.value[idx].text = `网络错误：${e}`; // fetch 自身失败、断网等
  } finally {
    messages.value[idx].streaming = false; // 关闭「思考中」态
    loading.value = false; // 恢复按钮
    await scrollToBottom();
  }
}

function onKey(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault(); // 阻止 textarea 默认插入换行
    send(); // 改为发送消息
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
