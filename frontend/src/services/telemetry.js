import api from './api';

/**
 * 轻量行为埋点。
 *
 * 原则：埋点永远不能影响功能。
 *  - fire-and-forget，不 await、不弹错、失败静默；
 *  - 页面浏览做短时间去重，避免 StrictMode 双渲染和快速来回跳转把表刷满；
 *  - 未登录直接不发，避免 401 触发全局登出跳转。
 */

const DEDUPE_MS = 4000;
const lastSent = new Map();

function shouldSkip(key) {
  const now = Date.now();
  const prev = lastSent.get(key);
  if (prev && now - prev < DEDUPE_MS) return true;
  lastSent.set(key, now);
  // 防止 Map 无限增长
  if (lastSent.size > 200) {
    for (const [k, t] of lastSent) {
      if (now - t > DEDUPE_MS) lastSent.delete(k);
    }
  }
  return false;
}

export function track(event, meta) {
  if (!localStorage.getItem('token')) return;
  if (!event) return;

  const { path, ...rest } = meta || {};
  const targetPath = path || window.location.pathname;
  const key = `${event}|${targetPath}`;
  if (shouldSkip(key)) return;

  api
    .post('/events/ui', {
      event,
      path: targetPath,
      meta: Object.keys(rest).length ? rest : undefined,
    })
    .catch(() => {
      /* 埋点失败不打扰用户 */
    });
}

export function trackPageView(path) {
  track('page_view', { path });
}

export default { track, trackPageView };
