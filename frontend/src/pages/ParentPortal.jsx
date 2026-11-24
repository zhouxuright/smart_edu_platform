import React, { useEffect, useState, useRef } from 'react';
import { Card, Typography, Spin, Alert, Select, Input, Button, Empty, Space, Popconfirm, message } from 'antd';
import { UserAddOutlined, LinkOutlined, DisconnectOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { agentService, parentLinkService, API_URL } from '../services/api';
import { getRole } from '../services/session';

const { Title, Paragraph, Text } = Typography;

/**
 * 家长沟通门户。
 *
 * 这个页面之前是假的：它拿 localStorage 里的 user_id（登录者自己的 id）
 * 去请求「家长报告」，所以家长看到的其实是**自己**的空数据，标题却写着
 * 「孩子的学习报告」。根因是家长与学生的绑定关系根本没建模。
 *
 * 现在的流程：学生生成邀请码 → 家长用码绑定 → 这里列出孩子 → 选一个看报告。
 * 没有绑定就明确让你去绑，而不是渲染一份空报告假装有数据。
 */
const ParentPortal = () => {
  const navigate = useNavigate();
  const [children, setChildren] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [childrenLoading, setChildrenLoading] = useState(true);
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [code, setCode] = useState('');
  const [binding, setBinding] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const controllerRef = useRef(null);
  const role = getRole();
  // 显式判断而不是 `!== 'student'`：老会话里 role 可能读不到，
  // 那种情况应该退化成「不是家长」，而不是反过来。
  const isParent = role === 'parent' || role === 'admin';

  const parsePlan = (text) => {
    if (!text) return null;
    const has = /目标|思考|行动步骤|下一步/.test(text);
    if (!has) return null;
    const getBlock = (label) => {
      const m = text.match(new RegExp(label + "[\\s\\S]*?(?=\\n\\s*[\\u76ee\\u601d\\u884c\\u7ed3\\u4e0b]|$)"));
      return m ? m[0].replace(new RegExp("^" + label + "\\s*:?"), '').trim() : '';
    };
    const goal = getBlock('目标');
    const thinking = getBlock('思考');
    const steps = getBlock('行动步骤');
    const next = getBlock('结果/下一步') || getBlock('下一步');
    const stepsList = steps ? steps.split(/\n|；/).map(s => s.replace(/^[-•\d\.\)\s]*/, '').trim()).filter(Boolean) : [];
    return { goal, thinking, stepsList, next };
  };

  const fetchReport = async (studentId, controller) => {
    if (!studentId) return;
    setLoading(true);
    setReport(null);
    try {
      const token = localStorage.getItem('token');
      const resp = await fetch(`${API_URL}/agent/parent/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ student_id: Number(studentId) }),
        signal: controller?.signal,
      });
      if (!resp.ok || !resp.body) {
        if (resp.status === 401) {
          localStorage.removeItem('token');
          messageApi.error('登录已过期，请重新登录');
          setTimeout(() => window.location.href = '/login', 1000);
          return;
        }
        const text = await resp.text();
        let detail = text || `HTTP ${resp.status}`;
        try { detail = JSON.parse(text).detail || detail; } catch (e) { /* 非 JSON 就用原文 */ }
        throw new Error(detail);
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      let result = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split(/\r?\n\r?\n/);
        buffer = parts.pop();
        for (const evt of parts) {
          const line = evt.trim();
          if (!line.startsWith('data:')) continue;
          const dataStr = line.slice(5).trim();
          if (dataStr === '[DONE]') continue;
          let content = dataStr;
          try {
            const json = JSON.parse(dataStr);
            if (json.content !== undefined) content = json.content;
          } catch (e) { /* 纯文本片段 */ }
          if (content) {
            result += content;
            setReport(result);
          }
        }
      }
      if (!result.trim()) {
        const response = await agentService.getParentReport(studentId, { signal: controller?.signal });
        setReport(response.data.report);
      }
    } catch (error) {
      const name = error?.name || '';
      const msg = error?.message || '';
      if (name === 'AbortError' || msg.includes('aborted') || msg.includes('canceled')) return;
      messageApi.error(msg ? `获取家长报告失败：${msg}` : '获取家长报告失败');
    } finally {
      setLoading(false);
    }
  };

  const loadChildren = async () => {
    setChildrenLoading(true);
    try {
      const { data } = await parentLinkService.children();
      const items = data.items || [];
      setChildren(items);
      if (items.length) {
        const first = selectedId && items.some((i) => i.student_id === selectedId)
          ? selectedId
          : items[0].student_id;
        setSelectedId(first);
        const controller = new AbortController();
        controllerRef.current = controller;
        fetchReport(first, controller);
      }
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || '读取绑定列表失败');
    } finally {
      setChildrenLoading(false);
    }
  };

  useEffect(() => {
    if (!isParent) {
      setChildrenLoading(false);
      return undefined;
    }
    loadChildren();
    return () => { if (controllerRef.current) controllerRef.current.abort(); };
  }, []);

  const handleBind = async () => {
    const value = code.trim().toUpperCase();
    if (!value) {
      messageApi.warning('请填写孩子给你的邀请码');
      return;
    }
    setBinding(true);
    try {
      const { data } = await parentLinkService.bind(value);
      messageApi.success(data.already_bound ? '这个孩子之前已经绑定过了' : '绑定成功');
      setCode('');
      await loadChildren();
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || '绑定失败，请检查邀请码');
    } finally {
      setBinding(false);
    }
  };

  const handleUnbind = async (studentId) => {
    try {
      await parentLinkService.unbind(studentId);
      messageApi.success('已解绑');
      setSelectedId(null);
      setReport(null);
      await loadChildren();
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || '解绑失败');
    }
  };

  const onSelect = (value) => {
    setSelectedId(value);
    if (controllerRef.current) controllerRef.current.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    fetchReport(value, controller);
  };

  // 学生进到这个页面没有意义：接口会 403，页面也不该假装能看
  if (!isParent) {
    return (
      <div>
        {contextHolder}
        <Title level={3} style={{ margin: 0 }}>家长沟通门户</Title>
        <Card style={{ marginTop: 24 }}>
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="这是给家长账号用的页面。你想让家长看到自己的学习情况，可以在仪表盘生成一个邀请码。"
          >
            <Button type="primary" onClick={() => navigate('/dashboard')}>
              去仪表盘生成邀请码
            </Button>
          </Empty>
        </Card>
      </div>
    );
  }

  return (
    <div>
      {contextHolder}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Title level={3} style={{ margin: 0 }}>家长沟通门户</Title>
        {children.length > 0 && (
          <Space>
            <Select
              value={selectedId}
              onChange={onSelect}
              style={{ minWidth: 180 }}
              options={children.map((c) => ({ value: c.student_id, label: c.name }))}
            />
            <Popconfirm
              title="解绑后你将看不到这个孩子的学情"
              onConfirm={() => handleUnbind(selectedId)}
              okText="解绑"
              cancelText="取消"
            >
              <Button icon={<DisconnectOutlined />}>解绑</Button>
            </Popconfirm>
          </Space>
        )}
      </div>

      {childrenLoading ? (
        <div style={{ textAlign: 'center', padding: 50 }}>
          <Spin size="large" />
        </div>
      ) : children.length === 0 ? (
        <Card style={{ marginTop: 24 }} title="绑定孩子">
          <Paragraph type="secondary">
            邀请码由孩子在自己的仪表盘生成（6 位，7 天内有效，只能用一次）。
            绑定之后你才能看到他的学习进度、错题和阶段评估。
          </Paragraph>
          <Space.Compact style={{ width: '100%', maxWidth: 420 }}>
            <Input
              placeholder="输入 6 位邀请码"
              value={code}
              maxLength={6}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              onPressEnter={handleBind}
              prefix={<LinkOutlined />}
            />
            <Button type="primary" icon={<UserAddOutlined />} loading={binding} onClick={handleBind}>
              绑定
            </Button>
          </Space.Compact>
        </Card>
      ) : (
        <>
          <Alert
            message="每周更新"
            description={`这是 AI 根据 ${children.find((c) => c.student_id === selectedId)?.name || '孩子'} 最近的学习记录生成的进度报告。`}
            type="info"
            showIcon
            style={{ marginTop: 16, marginBottom: 24 }}
          />
          {loading && !report ? (
            <div style={{ textAlign: 'center', padding: 50 }}>
              <Spin size="large" />
            </div>
          ) : report ? (
            <Card title="学生进度报告">
              {(() => {
                const plan = parsePlan(report);
                if (!plan) {
                  return <div style={{ whiteSpace: 'pre-wrap', lineHeight: '1.6', fontSize: 16 }}>{report}</div>;
                }
                return (
                  <div style={{ lineHeight: '1.6', fontSize: 16 }}>
                    {plan.goal && <div><Text strong>目标：</Text>{plan.goal}</div>}
                    {plan.thinking && <div style={{ marginTop: 6 }}><Text strong>思考：</Text>{plan.thinking}</div>}
                    {plan.stepsList && plan.stepsList.length > 0 && (
                      <div style={{ marginTop: 6 }}>
                        <Text strong>行动步骤：</Text>
                        <ul style={{ paddingLeft: 18 }}>
                          {plan.stepsList.map((s, i) => (<li key={i}>{s}</li>))}
                        </ul>
                      </div>
                    )}
                    {plan.next && <div style={{ marginTop: 6 }}><Text strong>下一步：</Text>{plan.next}</div>}
                  </div>
                );
              })()}
            </Card>
          ) : (
            <Card><Empty description="还没有生成报告" /></Card>
          )}
        </>
      )}
    </div>
  );
};

export default ParentPortal;
