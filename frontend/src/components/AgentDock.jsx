import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Input, Tag, Spin, message } from 'antd';
import {
  ArrowRightOutlined,
  CheckOutlined,
  CloseOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { useLocation } from 'react-router-dom';
import { pageAgentService } from '../services/api';
import {
  PAGE_LABEL,
  getAgentPageId,
  notifyAgentApplied,
  pageKeyForPath,
  subscribeAgentPageId,
} from '../services/agentPage';
import './AgentDock.css';

const GREETING = {
  resume: '我能读到你正在编辑的这份简历。想改哪段、觉得哪句写得虚，直接说；改动我会先摆出来给你看。',
  applications: '我能看到你的投递记录。可以帮你判断哪条该催、哪条该放，也能帮你把状态往前推。',
  plan: '我能看到你这份 7 天计划和完成进度。可以帮你判断今天该做哪一步，也能直接帮你勾掉做完的。',
  'mistake-book': '我能读到你的错题本：哪些题到了该复习的时候、哪一科错得最多。把题干粘给我就能收录，也能帮你标记掌握。',
  parent: '我能看到这个账号的学习记录、错题、计划进度和投递情况，可以把它翻译成家长能听懂的话。',
  knowledge: '我能把你的错题和低分记录按知识点排成一张薄弱清单，告诉你先补哪块、它的前置知识是什么。',
  dashboard: '我能把计划、错题、投递、薄弱点四处合起来看，告诉你现在最该做哪一件事，以及为什么是它。',
  other: '这个页面还没接入数据，我只能给通用建议。简历、投递、计划、错题本、知识图谱、仪表盘这几个页面我能读到真实数据。',
};

const SUGGESTIONS = {
  resume: ['这份简历最缺什么', '第一段实习的要点帮我改得更有结果感', '技能栏怎么补'],
  applications: ['我现在投递情况怎么样', '哪条最该跟进', '帮我排一下这周的跟进顺序'],
  plan: ['我今天该做哪一步', '我这周进度怎么样', '第一天那个任务帮我标成完成'],
  'mistake-book': ['我哪几道题最该复习', '哪一科错得最多', '帮我收录一道错题'],
  parent: ['这周学得怎么样', '哪里最需要补', '有没有该跟进的事'],
  knowledge: ['我最该补哪个知识点', '这些薄弱点有先后顺序吗', '哪一科问题最多'],
  dashboard: ['我现在最该做什么', '为什么先做这个而不是别的', '我今天还有哪些没做完'],
  other: ['启程 AI 能帮我做什么', '简历怎么写才有结果感'],
};

function errorText(error) {
  const status = error?.response?.status;
  const detail = error?.response?.data?.detail;
  if (status === 429) return '今天的 AI 额度用完了，明天再来。';
  if (status === 401) return '登录状态失效了，请重新登录。';
  if (status === 503) return detail || 'AI 暂时不可用，请稍后再试。';
  if (typeof detail === 'string') return detail;
  return '请求没成功，检查下网络再试。';
}

/* ------------------------------------------------------------------ 提案卡片 */

function BulletsDiff({ preview }) {
  const before = preview.before || [];
  const after = preview.after || [];
  return (
    <div className="agent-diff">
      <div className="agent-diff-col">
        <div className="agent-diff-label">现在</div>
        {before.length ? (
          before.map((line, index) => (
            <p className="agent-diff-line is-before" key={`b-${index}`}>{line}</p>
          ))
        ) : (
          <p className="agent-diff-line is-empty">（原来没有要点）</p>
        )}
      </div>
      <div className="agent-diff-col is-after">
        <div className="agent-diff-label">改后</div>
        {after.map((line, index) => (
          <p className="agent-diff-line is-after" key={`a-${index}`}>{line}</p>
        ))}
      </div>
    </div>
  );
}

function StatusFlow({ preview }) {
  return (
    <div className="agent-status-flow">
      <span className="agent-chip">{preview.from_label || preview.from}</span>
      <ArrowRightOutlined className="agent-chip-arrow" />
      <span className="agent-chip is-new">{preview.to_label || preview.to}</span>
    </div>
  );
}

/** 新增类改动：没有「改前」，所以直接展示即将写入的条目 */
function ItemPreview({ preview }) {
  return (
    <div className="agent-diff-col is-after">
      {preview.meta && <p className="agent-item-meta">{preview.meta}</p>}
      {(preview.bullets || []).map((line, index) => (
        <p className="agent-diff-line is-after" key={`i-${index}`}>{line}</p>
      ))}
    </div>
  );
}

/** 整段文字：前后各一段，改成行内对比更直观 */
function TextDiff({ preview }) {
  return (
    <div className="agent-diff">
      <div className="agent-diff-col">
        <div className="agent-diff-label">现在</div>
        {preview.before
          ? <p className="agent-diff-para is-before">{preview.before}</p>
          : <p className="agent-diff-para is-empty">（原来是空的）</p>}
      </div>
      <div className="agent-diff-col is-after">
        <div className="agent-diff-label">改后</div>
        <p className="agent-diff-para is-after">{preview.after}</p>
      </div>
    </div>
  );
}

function PreviewBody({ preview }) {
  if (preview.kind === 'status') return <StatusFlow preview={preview} />;
  if (preview.kind === 'item') return <ItemPreview preview={preview} />;
  if (preview.kind === 'text') return <TextDiff preview={preview} />;
  return <BulletsDiff preview={preview} />;
}

function ProposalCard({ proposal, decided, busy, onDecide }) {
  const preview = proposal.preview || {};
  const done = decided === 'applied';
  const rejected = decided === 'rejected';
  const expired = decided === 'expired';
  const closed = done || rejected || expired;

  return (
    <div className={done ? 'agent-proposal is-done' : closed ? 'agent-proposal is-dropped' : 'agent-proposal'}>
      <div className="agent-proposal-head">
        <span className="agent-proposal-title">{proposal.summary}</span>
        {done && <Tag color="green" bordered={false}>已应用</Tag>}
        {rejected && <Tag bordered={false}>已放弃</Tag>}
        {expired && <Tag bordered={false}>已失效</Tag>}
      </div>

      <PreviewBody preview={preview} />

      {preview.note && !closed && (
        <p className="agent-proposal-note">{preview.note}</p>
      )}

      {!closed && (
        <div className="agent-proposal-actions">
          <Button
            type="primary"
            size="small"
            icon={<CheckOutlined />}
            loading={busy}
            onClick={() => onDecide(true)}
          >
            应用
          </Button>
          <Button
            size="small"
            icon={<StopOutlined />}
            disabled={busy}
            onClick={() => onDecide(false)}
          >
            放弃
          </Button>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ 气泡 */

function Bubble({ item, index, busyId, onDecide }) {
  if (item.role === 'user') {
    return <div className="agent-msg is-user">{item.content}</div>;
  }
  return (
    <div className="agent-msg is-bot">
      {item.loading ? (
        <span className="agent-typing"><Spin size="small" /> 正在看数据…</span>
      ) : (
        <>
          {item.error ? (
            <p className="agent-error">{item.error}</p>
          ) : (
            <p className="agent-text">{item.content}</p>
          )}
          {item.notice && <p className="agent-notice">{item.notice}</p>}
          {item.proposal && (
            <ProposalCard
              proposal={item.proposal}
              decided={item.decided}
              busy={busyId === item.proposal.id}
              onDecide={(accept) => onDecide(index, item.proposal, accept)}
            />
          )}
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ 主体 */

const AgentDock = ({ standalone = false }) => {
  const { pathname } = useLocation();
  const page = useMemo(() => pageKeyForPath(pathname), [pathname]);
  const pageRef = useRef(page);
  pageRef.current = page;

  const [pageId, setPageId] = useState(() => getAgentPageId());
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [toolCount, setToolCount] = useState(null);
  const [messageApi, contextHolder] = message.useMessage();

  const listRef = useRef(null);
  const inputRef = useRef(null);

  // 页面登记的对象 id 变了就同步过来
  useEffect(() => subscribeAgentPageId(setPageId), []);

  // 换页面就重开会话：上一页的上下文留着只会误导模型
  useEffect(() => {
    setMessages([{ role: 'assistant', content: GREETING[page] || GREETING.other }]);
    setInput('');
    setToolCount(null);
    pageAgentService
      .tools(page)
      .then(({ data }) => setToolCount((data.tools || []).length))
      .catch(() => setToolCount(null));
  }, [page]);

  useEffect(() => {
    if (!open) return;
    const node = listRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages, open]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const send = useCallback(
    async (text) => {
      const content = String(text ?? input).trim();
      if (!content || sending) return;

      const history = messages
        .filter((item) => !item.loading && !item.error && item.content)
        .map((item) => ({ role: item.role, content: item.content }));

      setMessages((prev) => [
        ...prev,
        { role: 'user', content },
        { role: 'assistant', loading: true },
      ]);
      setInput('');
      setSending(true);

      try {
        const { data } = await pageAgentService.chat({
          page: pageRef.current,
          pageId,
          message: content,
          history,
        });
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = {
            role: 'assistant',
            content: data.reply,
            proposal: data.proposal || null,
            notice: data.notice || null,
          };
          return next;
        });
      } catch (error) {
        const text = errorText(error);
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = { role: 'assistant', error: text };
          return next;
        });
      } finally {
        setSending(false);
      }
    },
    [input, messages, pageId, sending],
  );

  const decide = useCallback(
    async (index, proposal, accept) => {
      setBusyId(proposal.id);
      try {
        const { data } = await pageAgentService.confirm(proposal.id, accept);
        setMessages((prev) =>
          prev.map((item, i) =>
            i === index ? { ...item, decided: accept ? 'applied' : 'rejected' } : item,
          ),
        );
        if (accept) {
          messageApi.success('已应用');
          // 页面收到这个事件后自己重拉数据，dock 不去猜各页的刷新方式
          notifyAgentApplied({ tool: data.tool, result: data.result });
        }
      } catch (error) {
        messageApi.error(errorText(error));
        // 提案已过期或已被处理，本地这张卡就不能再点了；标「失效」而不是「放弃」，
        // 否则用户会以为自己点过放弃
        setMessages((prev) =>
          prev.map((item, i) => (i === index ? { ...item, decided: 'expired' } : item)),
        );
      } finally {
        setBusyId(null);
      }
    },
    [messageApi],
  );

  const onKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  };

  const label = PAGE_LABEL[page] || PAGE_LABEL.other;
  const suggestions = SUGGESTIONS[page] || SUGGESTIONS.other;
  const showSuggestions = messages.length <= 1 && !sending;

  return (
    <>
      {contextHolder}

      {!open && (
        <button
          type="button"
          className={standalone ? 'agent-fab is-standalone' : 'agent-fab'}
          onClick={() => setOpen(true)}
          aria-label={`打开${label}助手`}
        >
          <RobotOutlined />
          <span className="agent-fab-text">助手</span>
        </button>
      )}

      {open && (
        <section
          className={standalone ? 'agent-panel is-standalone' : 'agent-panel'}
          aria-label={`${label}助手`}
        >
          <header className="agent-panel-head">
            <div className="agent-panel-title">
              <RobotOutlined />
              <span>启程助手</span>
              <Tag bordered={false} className="agent-panel-tag">{label}</Tag>
            </div>
            <Button
              type="text"
              size="small"
              icon={<CloseOutlined />}
              onClick={() => setOpen(false)}
              aria-label="收起助手"
            />
          </header>

          {toolCount === 0 && (
            <p className="agent-panel-hint">
              这个页面还没接入数据，回答只基于通用经验。想看真实数据，去「简历工作台」「投递看板」或「错题本」。
            </p>
          )}

          <div className="agent-list" ref={listRef}>
            {messages.map((item, index) => (
              <Bubble
                key={`${item.role}-${index}`}
                item={item}
                index={index}
                busyId={busyId}
                onDecide={decide}
              />
            ))}

            {showSuggestions && (
              <div className="agent-suggestions">
                {suggestions.map((text) => (
                  <button type="button" key={text} onClick={() => send(text)}>
                    {text}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="agent-input">
            <Input.TextArea
              ref={inputRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={onKeyDown}
              placeholder={toolCount === 0 ? '问点通用的也行…' : '说说想改什么，或直接问…'}
              autoSize={{ minRows: 1, maxRows: 4 }}
              maxLength={2000}
              disabled={sending}
            />
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={() => send()}
              loading={sending}
              disabled={!input.trim()}
              aria-label="发送"
            />
          </div>
        </section>
      )}
    </>
  );
};

export default AgentDock;
