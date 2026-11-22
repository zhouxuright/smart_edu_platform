import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ArrowLeft, Send, Sparkles } from 'lucide-react';
import { agentService, API_URL, authService, quotaService } from '../services/api';
import { CAREER_MENTOR_PATH, CAREER_QUESTION_LIMIT, clearMentorDraft, readMentorDraft, saveMentorDraft } from '../services/mentorDraft';
import MarkdownView from '../components/MarkdownView';
import './MentorAgent.css';

async function readMentorStream(response, onContent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let text = '';
  let source = '';
  let completed = false;
  function processLine(line) {
    if (!line.startsWith('data:')) return;
    const data = line.slice(5).trim();
    if (data === '[DONE]') { completed = true; return; }
    if (!data) return;
    let event;
    try { event = JSON.parse(data); } catch { event = { content: data }; }
    if (event?.error) throw new Error('mentor_unavailable');
    if (event?.source) source = event.source;
    if (typeof event?.content === 'string') {
      text += event.content;
      onContent(text, source);
    }
  }
  try {
    while (!completed) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop();
      for (const line of lines) { processLine(line); if (completed) break; }
    }
    if (!completed) processLine(buffer + decoder.decode());
    return { text, source };
  } finally {
    if (completed) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export default function MentorAgent() {
  const location = useLocation();
  const career = new URLSearchParams(location.search).get('focus') === 'career';
  if (career) return <main className="mentor-standalone"><MentorConversation key="career" /></main>;
  return <MentorConversation key="study" />;
}

function MentorConversation() {
  const career = new URLSearchParams(useLocation().search).get('focus') === 'career';
  const navigate = useNavigate();
  const initialQuestion = useRef(career ? readMentorDraft() : '');
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState(initialQuestion.current);
  const [restored, setRestored] = useState(Boolean(initialQuestion.current));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [quota, setQuota] = useState(null);
  const request = useRef(null);
  const bottom = useRef(null);
  const returnTo = career ? CAREER_MENTOR_PATH : '/mentor';
  useEffect(() => () => request.current?.abort(), []);

  useEffect(() => {
    let alive = true;
    quotaService.status()
      .then((res) => { if (alive) setQuota(res.data); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  useEffect(() => { if (messages.length) bottom.current?.scrollIntoView({ block: 'nearest' }); }, [messages]);

  async function send(event) {
    event.preventDefault();
    const question = input.trim();
    if (request.current || !question) return;
    const controller = new AbortController();
    request.current = controller;
    const history = messages.filter((item) => item.content && item.source !== 'fallback').slice(-5).map(({ role, content }) => ({ role, content }));
    const withQuestion = [...messages, { role: 'user', content: question }];
    const renderAnswer = (content, source = '') => setMessages([...withQuestion, { role: 'assistant', content, source }]);
    setMessages(withQuestion);
    setInput('');
    setRestored(false);
    setError('');
    setLoading(true);
    const token = localStorage.getItem('token');
    try {
      const response = await fetch(`${API_URL}/agent/mentor/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ message: question, history, mode: career ? 'career' : 'study' }),
        signal: controller.signal,
      });
      if (response.status === 401) {
        if (career) saveMentorDraft(question);
        authService.logout();
        navigate('/login', { replace: true, state: { returnTo } });
        return;
      }
      if (response.status === 429) {
        let detail = '今日 AI 额度已用完，明天 0 点重置。';
        try {
          const body = await response.json();
          if (body?.detail) detail = body.detail;
        } catch { /* 用默认文案 */ }
        setMessages(messages);
        setInput(question);
        setError(detail);
        return;
      }
      if (!response.ok || !response.body) throw new Error('mentor_unavailable');
      const result = await readMentorStream(response, renderAnswer);
      if (!result.text.trim()) {
        if (career) throw new Error('empty_answer');
        const reply = await agentService.mentorChat(question, history, { timeout: 30000, signal: controller.signal });
        if (!reply?.data?.response?.trim()) throw new Error('empty_answer');
        renderAnswer(reply.data.response, reply.data.source === 'fallback' ? 'fallback' : '');
      }
      if (career) clearMentorDraft();
    } catch {
      if (!controller.signal.aborted) {
        setMessages(messages);
        setInput(question);
        setError('暂时没有收到回答。问题已保留，请稍后重试。');
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
      if (request.current === controller) request.current = null;
    }
  }

  return <section className={`mentor-workspace${career ? ' mentor-career' : ''}`} aria-labelledby="mentor-title">
    <header className="mentor-header"><div><h1 id="mentor-title">{career ? 'AI 生涯导师' : 'AI 学习导师'}</h1><p>{career ? '说说你的目标、时间和最卡住的地方。' : '把想弄懂的问题讲清楚，我们一步步拆解。'}</p>{quota && !quota.unlimited && quota.limit ? <p className="mentor-quota" role="status">今日剩余 AI 额度 {quota.remaining}/{quota.limit}</p> : null}</div><Link to={career ? '/' : '/dashboard'}><ArrowLeft size={15} aria-hidden="true" /> {career ? '返回首页' : '返回仪表盘'}</Link></header>
    <div className="mentor-messages" aria-label="对话记录">
      {!messages.length && <div className="mentor-empty"><span><Sparkles size={22} aria-hidden="true" /></span><strong>{career ? '想先聊哪一步？' : '从一个问题开始'}</strong><p>{career ? '可以从考研、第一份实习，或者如何准备求职聊起。' : '写下具体的问题，导师会尽量给出可执行的建议。'}</p></div>}
      {messages.map((item, index) => <article key={index} className={`mentor-message mentor-message-${item.role}`}><span>{item.role === 'user' ? '你' : item.source === 'fallback' ? 'AI 导师 · 本地示例' : 'AI 导师'}</span>{item.role === 'user' ? <p>{item.content}</p> : <MarkdownView content={item.content} streaming={loading && index === messages.length - 1} />}</article>)}
      {loading && <p className="mentor-loading" role="status">正在生成回答…</p>}
      {career && !loading && messages.some((item) => item.role === 'assistant') && <div className="mentor-plan-cta"><p>想把建议变成能执行的动作？</p><Link to="/plan/internship">生成 7 天实习计划</Link></div>}
      <div ref={bottom} />
    </div>
    {restored && !error && <p className="mentor-notice" role="status">已恢复首页问题，你可以修改后再发送。</p>}
    {error && <p className="mentor-notice mentor-error" role="alert">{error}</p>}
    <form className="mentor-composer" onSubmit={send}>
      <label className="career-sr-only" htmlFor="mentor-question">向导师提问</label>
      <textarea id="mentor-question" rows={2} placeholder="写下你的问题…" value={input} maxLength={CAREER_QUESTION_LIMIT} disabled={loading} onChange={(event) => { setInput(event.target.value); if (career && restored) saveMentorDraft(event.target.value); }} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form.requestSubmit(); } }} />
      <button type="submit" disabled={loading || !input.trim()}>发送 <Send size={16} aria-hidden="true" /></button>
    </form>
    {career && <p className="mentor-disclaimer">AI 建议仅供参考；请勿输入证件号、密码等敏感信息。</p>}
  </section>;
}
