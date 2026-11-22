import React, { useState, useRef } from 'react';
import { Card, Input, Button, List, Typography, Divider, message } from 'antd';
import { TeamOutlined, UserOutlined } from '@ant-design/icons';
import { agentService, API_URL } from '../services/api';
import MarkdownView from '../components/MarkdownView';

const { Title, Text } = Typography;

const Collaborative = () => {
  const parsePlan = (text) => {
    if (!text) return null;
    const has = /思考|行动建议|角色分配|决定|下一步/.test(text);
    if (!has) return null;
    const getBlock = (label) => {
      const m = text.match(new RegExp(label + "[\\s\\S]*?(?=\\n\\s*[\\u601d\\u884c\\u89d2\\u51b3\\u4e0b]|$)"));
      return m ? m[0].replace(new RegExp("^" + label + "\\s*:?"), '').trim() : '';
    };
    const thinking = getBlock('思考');
    const advice = getBlock('行动建议');
    const roles = getBlock('角色分配');
    const next = getBlock('决定与下一步') || getBlock('下一步');
    const adviceList = advice ? advice.split(/\n|；/).map(s => s.replace(/^[-•\d\.\)\s]*/, '').trim()).filter(Boolean) : [];
    const rolesList = roles ? roles.split(/\n|；/).map(s => s.replace(/^[-•\d\.\)\s]*/, '').trim()).filter(Boolean) : [];
    return { thinking, adviceList, rolesList, next };
  };
  const [topic, setTopic] = useState('');
  const [discussion, setDiscussion] = useState([]);
  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [started, setStarted] = useState(false);
  const controllerRef = useRef(null);
  const [messageApi, contextHolder] = message.useMessage();

  const startDiscussion = () => {
    if (topic.trim()) {
      setStarted(true);
      setDiscussion([
        { user: '系统', content: `讨论已开始：${topic}。欢迎大家！` }
      ]);
    }
  };

  const handleSendMessage = async () => {
    if (!inputValue.trim()) return;

    const newMessage = { user: '我', content: inputValue };
    const updatedDiscussion = [...discussion, newMessage];
    setDiscussion(updatedDiscussion);
    setInputValue('');
    setLoading(true);

    try {
      if (controllerRef.current) controllerRef.current.abort();
      controllerRef.current = new AbortController();
      const token = localStorage.getItem('token');
      const resp = await fetch(`${API_URL}/agent/collaborative/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': `Bearer ${token}` } : {})
        },
        body: JSON.stringify({ topic, history: updatedDiscussion }),
        signal: controllerRef.current.signal,
      });
      if (!resp.ok || !resp.body) {
        if (resp.status === 401) {
          localStorage.removeItem('token');
          messageApi.error('登录已过期，请重新登录');
          setTimeout(() => window.location.href = '/login', 1000);
          return;
        }
        const text = await resp.text();
        throw new Error(text || `HTTP ${resp.status}`);
      }
      setDiscussion(prev => [...prev, { user: 'AI 助教', content: '正在生成...' }]);
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
          } catch (e) {}
          if (content) {
            result += content;
            setDiscussion(prev => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.user === 'AI 助教') {
                next[next.length - 1] = { user: 'AI 助教', content: result };
              }
              return next;
            });
          }
        }
      }
      if (!result.trim()) {
        try {
          const response = await agentService.collaborate(topic, updatedDiscussion, { signal: controllerRef.current.signal, timeout: 15000 });
          const text = response?.data?.response || '抱歉，本次未生成内容，请稍后重试。';
          setDiscussion(prev => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last && last.user === 'AI 助教') {
              next[next.length - 1] = { user: 'AI 助教', content: text };
            } else {
              next.push({ user: 'AI 助教', content: text });
            }
            return next;
          });
        } catch {}
      }
    } catch (error) {
      const name = error?.name || '';
      const msg = error?.message || '';
      // 增加对 AbortError 和 aborted 消息的检查
      if (name === 'CanceledError' || name === 'AbortError' || String(msg).includes('canceled') || String(msg).includes('aborted')) return;
      
      let detail = error?.response?.data?.detail;
      if (!detail && msg) {
         detail = msg;
      }
      
      messageApi.error(detail ? `协作失败：${detail}` : '协作失败');
    } finally {
      setLoading(false);
      controllerRef.current = null;
    }
  };

  return (
    <div>
      {contextHolder}
      <Title level={3}>协作学习空间</Title>
      
      {!started ? (
        <Card title="发起新讨论">
          <Input 
            placeholder="输入讨论主题..." 
            value={topic} 
            onChange={e => setTopic(e.target.value)}
            style={{ marginBottom: 16 }}
          />
          <Button type="primary" onClick={startDiscussion} disabled={!topic}>
            开始会话
          </Button>
        </Card>
      ) : (
        <div style={{ display: 'flex', gap: 24 }}>
          <Card title={`主题：${topic}`} style={{ flex: 2, height: 'calc(100vh - 200px)', display: 'flex', flexDirection: 'column' }}>
            <div style={{ flex: 1, overflowY: 'auto', marginBottom: 16 }}>
              <List
                dataSource={discussion}
                renderItem={item => (
                  <List.Item>
                    <List.Item.Meta
                      avatar={item.user === 'AI 助教' ? <TeamOutlined style={{ fontSize: 24, color: '#52c41a' }} /> : <UserOutlined style={{ fontSize: 24, color: 'var(--plan-ink)' }} />}
                      title={<Text style={{ color: 'var(--plan-ink)' }}>{item.user}</Text>}
                      description={(() => {
                        if (item.user !== 'AI 助教') return <Text style={{ color: 'var(--plan-muted)' }}>{item.content}</Text>;
                        const plan = parsePlan(item.content);
                        if (!plan) return <MarkdownView content={item.content} />;
                        return (
                          <div style={{ color: '#95de64' }}>
                            {plan.thinking && <div><strong>思考：</strong>{plan.thinking}</div>}
                            {plan.adviceList && plan.adviceList.length > 0 && (
                              <div style={{ marginTop: 6 }}>
                                <strong>行动建议：</strong>
                                <ul style={{ paddingLeft: 18 }}>
                                  {plan.adviceList.map((s, i) => (<li key={i}>{s}</li>))}
                                </ul>
                              </div>
                            )}
                            {plan.rolesList && plan.rolesList.length > 0 && (
                              <div style={{ marginTop: 6 }}>
                                <strong>角色分配：</strong>
                                <ul style={{ paddingLeft: 18 }}>
                                  {plan.rolesList.map((s, i) => (<li key={i}>{s}</li>))}
                                </ul>
                              </div>
                            )}
                            {plan.next && <div style={{ marginTop: 6 }}><strong>下一步：</strong>{plan.next}</div>}
                          </div>
                        );
                      })()}
                    />
                  </List.Item>
                )}
              />
            </div>
            <div style={{ display: 'flex' }}>
              <Input 
                value={inputValue}
                onChange={e => setInputValue(e.target.value)}
                onPressEnter={handleSendMessage}
                placeholder="参与讨论..."
                style={{ marginRight: 8 }}
                disabled={loading}
              />
              <Button type="primary" onClick={handleSendMessage} loading={loading}>
                发布
              </Button>
            </div>
          </Card>
          
          <Card title="参与者" style={{ flex: 1 }}>
            <List
              dataSource={['我', 'AI 助教']}
              renderItem={item => (
                <List.Item>
                  <Text>{item}</Text>
                </List.Item>
              )}
            />
          </Card>
        </div>
      )}
    </div>
  );
};

export default Collaborative;
