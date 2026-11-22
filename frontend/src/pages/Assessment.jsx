import React, { useEffect, useState, useRef } from 'react';
import { Card, Button, Typography, Spin, Descriptions, Select, Input, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { agentService, API_URL } from '../services/api';

const { Title, Paragraph } = Typography;

const Assessment = () => {
  const parsePlan = (text) => {
    if (!text) return null;
    const has = /目标|分析|行动步骤|下一步/.test(text);
    if (!has) return null;
    const getBlock = (label) => {
      // Add \u884c (行) and \u8bc4 (评) to the lookahead set
      const m = text.match(new RegExp(label + "[\\s\\S]*?(?=\\n\\s*[\\u76ee\\u5206\\u4e3e\\u5efa\\u63a8\\u63d0\\u4e0b\\u884c\\u8bc4]|$)"));
      return m ? m[0].replace(new RegExp("^" + label + "\\s*:?"), '').trim() : '';
    };
    const goal = getBlock('目标');
    const analysis = getBlock('分析');
    const steps = getBlock('行动步骤');
    const next = getBlock('评估与下一步') || getBlock('下一步');
    const stepsList = steps ? steps.split(/\n|；/).map(s => s.replace(/^[-•\d\.\)\s]*/, '').trim()).filter(Boolean) : [];
    return { goal, analysis, stepsList, next };
  };
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();
  const controllerRef = useRef(null);

  const fetchAssessment = async (controller) => {
    setLoading(true);
    try {
      const userId = localStorage.getItem('user_id');
      if (userId) {
        const token = localStorage.getItem('token');
        const resp = await fetch(`${API_URL}/agent/assessment/stream`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(token ? { 'Authorization': `Bearer ${token}` } : {})
          },
          body: JSON.stringify({ student_id: Number(userId) }),
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
          throw new Error(text || `HTTP ${resp.status}`);
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
            } catch (e) {}
            if (content) {
              result += content;
              setReport(result);
            }
          }
        }
        if (!result.trim()) {
          const response = await agentService.getAssessment(userId, { signal: controller?.signal });
          setReport(response.data.report);
        }
      }
    } catch (error) {
      const name = error?.name || '';
      const msg = error?.message || '';
      const code = error?.code || '';
      // 增加对 AbortError 和 aborted 消息的检查
      if (name === 'CanceledError' || name === 'AbortError' || msg.includes('canceled') || msg.includes('aborted') || code === 'ERR_CANCELED') return;
      
      let detail = error?.response?.data?.detail;
      // If it's a fetch error or custom Error from throw new Error(), use message
      if (!detail && msg) {
         detail = msg;
      }
      
      messageApi.error(detail ? `获取评估失败：${detail}` : '获取评估失败');
      console.error("Fetch assessment error:", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (controllerRef.current) controllerRef.current.abort();
    controllerRef.current = new AbortController();
    fetchAssessment(controllerRef.current);
    return () => { if (controllerRef.current) controllerRef.current.abort(); };
  }, []);

  return (
    <div>
      {contextHolder}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <Title level={3} style={{ margin: 0 }}>学习评估</Title>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Button icon={<ReloadOutlined />} onClick={() => {
            if (controllerRef.current) controllerRef.current.abort();
            controllerRef.current = new AbortController();
            fetchAssessment(controllerRef.current);
          }}>刷新分析</Button>
        </div>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 50 }}>
          <Spin spinning size="large" tip="正在分析您的学习记录...">
            <div style={{ width: 200, height: 80 }} />
          </Spin>
        </div>
      ) : report ? (
        <Card>
           {(() => {
             const plan = parsePlan(report);
             if (!plan) return <div style={{ whiteSpace: 'pre-wrap', lineHeight: '1.6' }}>{report}</div>;
             return (
               <div style={{ lineHeight: '1.6' }}>
                 {plan.goal && <div><strong>目标：</strong>{plan.goal}</div>}
                 {plan.analysis && <div style={{ marginTop: 6 }}><strong>分析：</strong>{plan.analysis}</div>}
                 {plan.stepsList && plan.stepsList.length > 0 && (
                   <div style={{ marginTop: 6 }}>
                     <strong>行动步骤：</strong>
                     <ul style={{ paddingLeft: 18 }}>
                       {plan.stepsList.map((s, i) => (<li key={i}>{s}</li>))}
                     </ul>
                   </div>
                 )}
                 {plan.next && <div style={{ marginTop: 6 }}><strong>下一步：</strong>{plan.next}</div>}
               </div>
             );
           })()}
        </Card>
      ) : (
        <Card>
          <p>暂无评估数据，开始学习以生成分析报告！</p>
        </Card>
      )}
    </div>
  );
};

export default Assessment;
