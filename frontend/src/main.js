// 前端入口：挂载 Vue 根组件并加载全局样式（暗色主题变量在 styles.css）
import { createApp } from "vue";
import App from "./App.vue";
import "./styles.css";

createApp(App).mount("#app"); // 与 index.html 中 #app 容器对应
