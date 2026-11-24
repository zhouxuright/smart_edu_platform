import { useEffect, useRef } from 'react';

/**
 * 页面上下文总线。
 *
 * Agent dock 挂在布局层，页面在内容层——两边没法靠 props 传值。
 * 页面只做一件事：把「当前操作的对象 id」登记进来（比如正在编辑哪份简历），
 * 其余判断都由 dock 按路由决定，页面不需要知道自己被 Agent 看着。
 */

const PAGE_BY_PATH = [
  [/^\/resume/, 'resume'],
  [/^\/applications/, 'applications'],
  [/^\/plan\/internship/, 'plan'],
  [/^\/mistake-book/, 'mistake-book'],
  [/^\/parent/, 'parent'],
  // 只匹配 /knowledge 本身与它的子路径，别把 /knowledge-extraction 也吞进来
  [/^\/knowledge(?:\/|$)/, 'knowledge'],
  [/^\/jd-match/, 'jd-match'],
  [/^\/mock-interview/, 'mock-interview'],
  [/^\/dashboard/, 'dashboard'],
];

export const PAGE_LABEL = {
  resume: '简历工作台',
  applications: '投递看板',
  plan: '我的计划',
  'mistake-book': '错题本',
  parent: '家长门户',
  knowledge: '知识图谱',
  'jd-match': 'JD 匹配',
  'mock-interview': '模拟面试',
  dashboard: '仪表盘',
  other: '启程 AI',
};

/** 路由 -> 后端认识的页面 key。没登记的路由一律 other（只做通用问答）。 */
export function pageKeyForPath(pathname) {
  const hit = PAGE_BY_PATH.find(([re]) => re.test(pathname || ''));
  return hit ? hit[1] : 'other';
}

let pageId = null;
const idListeners = new Set();

export function setAgentPageId(id) {
  const next = id === undefined || id === null || id === '' ? null : id;
  if (next === pageId) return;
  pageId = next;
  idListeners.forEach((fn) => fn(pageId));
}

export function getAgentPageId() {
  return pageId;
}

export function subscribeAgentPageId(fn) {
  idListeners.add(fn);
  return () => idListeners.delete(fn);
}

/**
 * 页面登记「当前对象」。
 * 卸载时清空，否则换到别的页面后 Agent 还会拿着上一页的 id 去查数据。
 */
export function useAgentPageId(id) {
  useEffect(() => {
    setAgentPageId(id);
    return () => setAgentPageId(null);
  }, [id]);
}

/* ---------------------------------------------------------------- 执行完成事件 */

export const AGENT_APPLIED = 'qicheng:agent-applied';

/** 提案执行成功后广播，页面据此重新拉数据——否则界面还停在旧值上 */
export function notifyAgentApplied(detail) {
  window.dispatchEvent(new CustomEvent(AGENT_APPLIED, { detail }));
}

export function useAgentApplied(handler) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const fn = (event) => ref.current?.(event.detail);
    window.addEventListener(AGENT_APPLIED, fn);
    return () => window.removeEventListener(AGENT_APPLIED, fn);
  }, []);
}
