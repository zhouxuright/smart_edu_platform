import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, Bell, BellRing, CalendarDays, Check, CheckCircle2, Loader2, RefreshCw, Sparkles, Target } from 'lucide-react';
import { planService } from '../services/api';
import { useAgentApplied } from '../services/agentPage';
import AgentDock from '../components/AgentDock';
import './InternshipPlan.css';

const STAGES = ['大一', '大二', '大三', '大四', '研一', '研二'];
const HOURS = [3, 5, 8, 10, 15];
const REMINDER_HOURS = [9, 12, 18, 20, 21];
const DAY_FEEDBACK = {
  1: '岗位标准已经落到纸面上，接下来把经历翻出来对照。',
  2: '素材齐了，下一步把它写成一页能投的简历。',
  3: '简历初稿有了，先补一个能证明能力的小作品。',
  4: '有作品可以讲了，明天开始定向投递。',
  5: '第一批投递完成，接着准备自我介绍和面试故事。',
  6: '表达准备好了，最后一步把数据复盘清楚。',
  7: '最后一天完成了，把复盘结果收好，接着准备面试。',
};
const todayKey = () => new Date().toISOString().slice(0, 10);

export default function InternshipPlan() {
  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState('');
  const [form, setForm] = useState({ target_role: '产品经理', stage: '大二', weekly_hours: 5 });
  const [generating, setGenerating] = useState(false);
  const [editing, setEditing] = useState(null);
  const [draftTitle, setDraftTitle] = useState('');
  const [feedback, setFeedback] = useState(null);
  const [reminderSaving, setReminderSaving] = useState(false);
  const [snoozed, setSnoozed] = useState(false);
  const titleRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const { data } = await planService.current();
      setPlan(data.plan || null);
      if (data.plan) {
        setForm({ target_role: data.plan.target_role, stage: data.plan.stage, weekly_hours: data.plan.weekly_hours });
        setSnoozed(localStorage.getItem(`plan-snooze-${data.plan.id}-${todayKey()}`) === '1');
      }
    } catch {
      setLoadError('暂时无法读取计划，请稍后重试。');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (editing) titleRef.current?.focus(); }, [editing]);

  // Agent 勾完任务后静默重拉：走 load() 会闪一下整页 loading，勾选这种小动作不值得
  useAgentApplied(({ tool }) => {
    if (tool !== 'toggle_task') return;
    planService.current()
      .then(({ data }) => { if (data.plan) setPlan(data.plan); })
      .catch(() => {});
  });
  useEffect(() => {
    planService.recordEvent('plan_opened', plan?.id).catch(() => {});
    // Only report once per page entry, not on every plan update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!plan?.reminder?.due || !plan.next_task || typeof Notification === 'undefined') return;
    if (Notification.permission !== 'granted') return;
    const key = `plan-notified-${plan.id}-${todayKey()}`;
    if (localStorage.getItem(key) === '1') return;
    try {
      new Notification('启程 AI · 今天的计划', { body: `第 ${plan.next_task.day_index} 天：${plan.next_task.title}`, tag: key });
      localStorage.setItem(key, '1');
    } catch {
      // Some browsers block notifications even after permission is granted.
    }
  }, [plan]);

  async function generate(event) {
    event?.preventDefault?.();
    if (!form.target_role.trim() || generating) return;
    if (plan && !window.confirm('重新生成会替换当前计划和已勾选的进度，确定继续吗？')) return;
    setGenerating(true);
    setActionError('');
    setFeedback(null);
    try {
      const { data } = await planService.generate({ ...form, target_role: form.target_role.trim(), replace: Boolean(plan) });
      setPlan(data.plan);
    } catch (error) {
      setActionError(error?.response?.status === 409 ? '已有一份计划，请刷新页面后查看。' : '生成失败，请稍后重试。');
    } finally {
      setGenerating(false);
    }
  }

  function nextPending(tasks) {
    return tasks.find((item) => !item.completed) || null;
  }

  async function patchTask(task, changes) {
    setSaving(true);
    setActionError('');
    const previous = plan;
    const optimisticTasks = plan.tasks.map((item) => (item.id === task.id ? { ...item, ...changes } : item));
    setPlan((current) => ({
      ...current,
      tasks: optimisticTasks,
      completed_count: optimisticTasks.filter((item) => item.completed).length,
      next_task: nextPending(optimisticTasks),
    }));
    try {
      const { data } = await planService.updateTask(task.id, changes);
      const tasks = optimisticTasks.map((item) => (item.id === task.id ? data.task : item));
      const next = nextPending(tasks);
      const done = tasks.filter((item) => item.completed).length;
      setPlan((current) => ({
        ...current,
        tasks,
        completed_count: done,
        next_task: next,
        reminder: current.reminder ? { ...current.reminder, due: current.reminder.enabled && Boolean(next) && new Date().getHours() >= current.reminder.hour } : current.reminder,
      }));
      if (changes.completed === true) {
        const firstEver = previous.completed_count === 0;
        const allDone = done === tasks.length;
        const body = allDone
          ? '7 天计划全部完成。下一步把投递记录整理成面试准备清单。'
          : DAY_FEEDBACK[task.day_index] || '这一项完成了，继续下一项。';
        setFeedback({ text: `${firstEver ? '第一步最难，你已经迈出去了。' : ''}${body}`, next, done });
      } else if (changes.completed === false) {
        setFeedback(null);
      }
    } catch {
      setPlan(previous);
      setActionError('这次修改没有保存成功，请重试。');
    } finally {
      setSaving(false);
    }
  }

  function startEdit(task) {
    setEditing(task.id);
    setDraftTitle(task.title);
  }

  function commitEdit(task) {
    const next = draftTitle.trim();
    setEditing(null);
    if (next.length >= 2 && next !== task.title) patchTask(task, { title: next });
    else if (next.length < 2) setActionError('任务名称至少需要 2 个字。');
  }

  async function changeReminder(enabled, hour = plan.reminder.hour) {
    setReminderSaving(true);
    setActionError('');
    try {
      if (enabled && typeof Notification !== 'undefined' && Notification.permission === 'default') {
        await Notification.requestPermission().catch(() => {});
      }
      const { data } = await planService.updateReminder(enabled, hour);
      const nowHour = new Date().getHours();
      setPlan((current) => ({ ...current, reminder: { ...data.reminder, due: data.reminder.enabled && Boolean(current.next_task) && nowHour >= data.reminder.hour } }));
    } catch {
      setActionError('提醒设置没有保存成功，请重试。');
    } finally {
      setReminderSaving(false);
    }
  }

  function snoozeToday() {
    localStorage.setItem(`plan-snooze-${plan.id}-${todayKey()}`, '1');
    setSnoozed(true);
  }

  function focusTask(task) {
    const node = document.getElementById(`plan-task-${task.id}`);
    node?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    node?.querySelector('input')?.focus({ preventScroll: true });
  }

  const done = plan?.completed_count ?? 0;
  const total = plan?.tasks.length ?? 0;
  const percent = total ? Math.round((done / total) * 100) : 0;
  const showDue = Boolean(plan?.reminder?.due && plan.next_task && !snoozed);

  return (
    <div className="plan-site">
      <header className="plan-header">
        <Link className="plan-brand" to="/"><span><Sparkles size={18} aria-hidden="true" /></span>启程 AI</Link>
        <nav aria-label="页面导航">
          <Link to="/mentor?focus=career"><ArrowLeft size={15} aria-hidden="true" /> 生涯导师</Link>
          <Link to="/">首页</Link>
        </nav>
      </header>

      <main className="plan-main">
        {loading ? <p className="plan-status" role="status"><Loader2 size={17} className="plan-spin" aria-hidden="true" /> 正在读取你的计划…</p>
          : loadError ? <div className="plan-status plan-error" role="alert">{loadError}<button type="button" onClick={load}>重新加载</button></div>
            : !plan ? (
              <section className="plan-setup" aria-labelledby="plan-setup-title">
                <p className="plan-kicker">第一份实习 · 7 天计划</p>
                <h1 id="plan-setup-title">先定一个小目标，<br />七天后你能投出第一批简历</h1>
                <p className="plan-lede">告诉我你想找的方向和能投入的时间，我会给出每天一个动作，并留下可检查的交付物。</p>
                <form className="plan-form" onSubmit={generate}>
                  <label>想找的实习方向<input value={form.target_role} maxLength={60} placeholder="例如：产品经理、前端开发、新媒体运营" onChange={(event) => setForm({ ...form, target_role: event.target.value })} required /></label>
                  <div className="plan-form-row">
                    <label>你现在是<select value={form.stage} onChange={(event) => setForm({ ...form, stage: event.target.value })}>{STAGES.map((stage) => <option key={stage} value={stage}>{stage}</option>)}</select></label>
                    <label>每周能投入<select value={form.weekly_hours} onChange={(event) => setForm({ ...form, weekly_hours: Number(event.target.value) })}>{HOURS.map((hours) => <option key={hours} value={hours}>{hours} 小时</option>)}</select></label>
                  </div>
                  <button className="plan-primary" type="submit" disabled={generating}>{generating ? <><Loader2 size={17} className="plan-spin" aria-hidden="true" /> 正在生成…</> : <>生成我的 7 天计划</>}</button>
                  {actionError && <p className="plan-inline-error" role="alert">{actionError}</p>}
                </form>
                <p className="plan-note">计划可以随时修改和勾选。AI 不承诺录取或录用结果，涉及招聘要求请以企业官方信息为准。</p>
              </section>
            ) : (
              <section className="plan-board" aria-labelledby="plan-title">
                <div className="plan-board-head">
                  <div>
                    <p className="plan-kicker">第一份实习 · 7 天计划</p>
                    <h1 id="plan-title">拿下「{plan.target_role}」的第一份实习</h1>
                    <p className="plan-meta"><span><Target size={15} aria-hidden="true" /> {plan.stage}</span><span><CalendarDays size={15} aria-hidden="true" /> 每周 {plan.weekly_hours} 小时</span><span aria-live="polite">{saving ? '保存中…' : '已保存'}</span></p>
                  </div>
                  <div className="plan-progress" aria-label={`已完成 ${done} / ${total}`}>
                    <strong>{done}<small> / {total}</small></strong>
                    <div className="plan-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}><span style={{ width: `${percent}%` }} /></div>
                    <span>{percent}% 完成</span>
                  </div>
                </div>

                {showDue && <div className="plan-reminder-banner" role="status">
                  <BellRing size={19} aria-hidden="true" />
                  <div><strong>今天的提醒</strong><p>第 {plan.next_task.day_index} 天 · {plan.next_task.title}，约 {plan.next_task.estimated_minutes} 分钟。</p></div>
                  <div className="plan-reminder-actions">
                    <button type="button" className="plan-secondary" onClick={() => focusTask(plan.next_task)}>开始这一项</button>
                    <button type="button" className="plan-ghost" onClick={snoozeToday}>今天不再提醒</button>
                  </div>
                </div>}

                {feedback && <div className="plan-feedback" role="status">
                  <CheckCircle2 size={19} aria-hidden="true" />
                  <div><strong>{feedback.text}</strong>{feedback.next ? <p>下一项：第 {feedback.next.day_index} 天 · {feedback.next.title}</p> : <p>7 项都完成了，可以回到生涯导师聊聊面试准备。</p>}</div>
                  {feedback.next && <button type="button" className="plan-feedback-next" onClick={() => focusTask(feedback.next)}>继续第 {feedback.next.day_index} 天</button>}
                </div>}

                <div className="plan-reminder-setting">
                  <label>
                    <input type="checkbox" checked={Boolean(plan.reminder?.enabled)} disabled={reminderSaving} onChange={(event) => changeReminder(event.target.checked)} />
                    <Bell size={15} aria-hidden="true" /> 每天提醒我完成当天任务
                  </label>
                  <select aria-label="提醒时间" value={plan.reminder?.hour ?? 20} disabled={reminderSaving || !plan.reminder?.enabled} onChange={(event) => changeReminder(true, Number(event.target.value))}>
                    {REMINDER_HOURS.map((hour) => <option key={hour} value={hour}>{String(hour).padStart(2, '0')}:00</option>)}
                  </select>
                  <span>提醒会在你打开本站时出现；关闭浏览器后不会推送。</span>
                </div>

                {plan.source === 'template' && <p className="plan-template-note">当前未接入真实模型，这是一份可编辑的内置模板计划，请结合自己的岗位方向调整。</p>}
                {actionError && <p className="plan-inline-error" role="alert">{actionError}</p>}

                <ol className="plan-days">
                  {plan.tasks.map((task) => (
                    <li key={task.id} id={`plan-task-${task.id}`} className={`plan-day${task.completed ? ' is-done' : ''}`}>
                      <div className="plan-day-label"><span>DAY {task.day_index}</span><small>约 {task.estimated_minutes} 分钟</small></div>
                      <div className="plan-day-body">
                        <label className="plan-check">
                          <input type="checkbox" checked={task.completed} onChange={(event) => patchTask(task, { completed: event.target.checked })} />
                          <span aria-hidden="true"><Check size={14} /></span>
                          <span className="plan-sr">标记第 {task.day_index} 天完成</span>
                        </label>
                        {editing === task.id ? (
                          <input ref={titleRef} className="plan-title-input" aria-label={`第 ${task.day_index} 天任务名称`} value={draftTitle} maxLength={60} onChange={(event) => setDraftTitle(event.target.value)} onBlur={() => commitEdit(task)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); commitEdit(task); } if (event.key === 'Escape') setEditing(null); }} />
                        ) : (
                          <button type="button" className="plan-task-title" onClick={() => startEdit(task)} title="点击修改任务名称">{task.title}</button>
                        )}
                        <p>{task.action}</p>
                        <p className="plan-deliverable"><strong>今天交什么</strong>{task.deliverable}</p>
                      </div>
                    </li>
                  ))}
                </ol>

                <div className="plan-actions">
                  <Link className="plan-secondary" to="/mentor?focus=career">回到生涯导师继续问</Link>
                  <button type="button" className="plan-ghost" onClick={generate} disabled={generating}><RefreshCw size={15} aria-hidden="true" /> {generating ? '生成中…' : '按新目标重新生成'}</button>
                </div>
              </section>
            )}
      </main>

      {/* 这一页是独立路由，不经过 MainLayout，dock 得自己挂 */}
      <AgentDock standalone />
    </div>
  );
}
