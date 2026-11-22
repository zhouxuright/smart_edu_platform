const { request, ensureAuth, toastError } = require('../../utils/request');

const MAX_HISTORY = 5;

/**
 * 小程序端没有 SSE，用非流式接口拿完整回答。
 * 后端返回的是 Markdown，这里做一次轻量清洗：气泡里只放纯文本，
 * 让用户看到排版结果而不是 `**` `###` 这类符号。
 */
function plain(text) {
  return String(text || '')
    .replace(/^```[\s\S]*?```$/gm, (block) => block.replace(/```\w*/g, ''))
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*\n]+)\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^\s*#{1,6}\s*/gm, '')
    .replace(/^\s*[-*_]{3,}\s*$/gm, '')
    .replace(/^\s*[-*]\s+/gm, '· ')
    .replace(/([\u4e00-\u9fff])\s*--\s*([\u4e00-\u9fff])/g, '$1——$2')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

Page({
  data: {
    messages: [],
    input: '',
    loading: false,
    mode: 'career',
    scrollTarget: '',
    quotaText: '',
  },

  onShow() {
    if (!ensureAuth()) return;
    this.loadQuota();
  },

  async loadQuota() {
    try {
      const quota = await request({ url: '/llm/quota' });
      if (quota && !quota.unlimited && quota.limit) {
        this.setData({ quotaText: `今日剩余 AI 额度 ${quota.remaining}/${quota.limit}` });
      } else {
        this.setData({ quotaText: '' });
      }
    } catch (error) {
      this.setData({ quotaText: '' });
    }
  },

  onInput(e) {
    this.setData({ input: e.detail.value });
  },

  switchMode(e) {
    this.setData({ mode: e.currentTarget.dataset.mode });
  },

  scrollToBottom() {
    const messages = this.data.messages;
    if (!messages.length) return;
    this.setData({ scrollTarget: `msg-${messages.length - 1}` });
  },

  async send() {
    const question = this.data.input.trim();
    if (!question || this.data.loading) return;

    const history = this.data.messages
      .filter((item) => item.role === 'user' || item.role === 'assistant')
      .slice(-MAX_HISTORY)
      .map((item) => ({ role: item.role, content: item.content }));

    const withQuestion = this.data.messages.concat([{ role: 'user', content: question }]);
    this.setData({ messages: withQuestion, input: '', loading: true });
    this.scrollToBottom();

    try {
      const res = await request({
        url: '/agent/mentor',
        method: 'POST',
        data: { message: question, history, mode: this.data.mode },
        timeout: 90000,
      });
      const answer = plain(res && res.response);
      this.setData({
        messages: withQuestion.concat([
          { role: 'assistant', content: answer || '这次没有收到回答，请再问一次。', fallback: res && res.source === 'fallback' },
        ]),
        loading: false,
      });
      this.loadQuota();
    } catch (error) {
      this.setData({ messages: withQuestion, input: question, loading: false });
      if (error.status === 429) {
        wx.showToast({ title: '今日 AI 额度已用完', icon: 'none', duration: 2400 });
      } else {
        toastError(error);
      }
    }
    this.scrollToBottom();
  },
});
