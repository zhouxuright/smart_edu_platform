import React, { useMemo, useState, useEffect, useRef } from 'react';
import { Card, Form, Input, Button, Empty, Typography, Tag, message, Modal, Alert } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import { agentService, API_URL } from '../services/api';
// 注意：@antv/g6 不在这里静态导入。它有 1.3MB（gzip 后 392KB），
// 静态 import 会把它算进本页首包，学生用手机打开要白等好几秒。
// 改成在下面真正要渲染图谱时动态 import，构建时会自动拆成独立分块。

const { Title, Paragraph } = Typography;

const KnowledgeGraph = () => {
  const [graphData, setGraphData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const g6Ref = useRef(null);
  const graphRef = useRef(null);
  const controllerRef = useRef(null);
  const [messageApi, contextHolder] = message.useMessage();
  const [lastParams, setLastParams] = useState(null);
  const [lastGraph, setLastGraph] = useState(null);
  const [timeoutHint, setTimeoutHint] = useState(false);

  useEffect(() => {
    // If we have no data, don't try to render
    if (!graphData || !g6Ref.current) return;
    
    let cancelled = false;

    const render = async () => {
      const { Graph } = await import('@antv/g6');
      // 分块加载是异步的：等它回来时组件可能已经卸载或换了数据，必须再确认一次
      if (cancelled || !g6Ref.current) return;

      // Cleanup previous graph instance
      if (graphRef.current) {
        graphRef.current.destroy();
        graphRef.current = null;
      }

    const width = g6Ref.current.clientWidth || 800;
    const height = g6Ref.current.clientHeight || 520;

    // Initialize G6 Graph
    const graph = new Graph({
      container: g6Ref.current,
      width,
      height,
      layout: { 
        type: 'force', 
        preventOverlap: true,
        linkDistance: 150,
        nodeStrength: -30,
        edgeStrength: 0.1
      },
      modes: { default: ['drag-node', 'zoom-canvas', 'drag-canvas'] },
      defaultNode: {
        size: 30,
        style: {
          fill: '#C6E5FF',
          stroke: '#5B8FF9',
        },
        labelCfg: {
          style: {
            fill: '#000',
            fontSize: 12,
          },
        },
      },
      defaultEdge: {
        style: {
          stroke: '#e2e2e2',
        },
      },
    });

    const nodes = (graphData.nodes || []).map(n => ({ 
      id: String(n.id), 
      label: n.label 
    }));
    
    const edges = (graphData.edges || []).map(e => ({ 
      source: String(e.source), 
      target: String(e.target), 
      label: e.relationship 
    }));

    // Only render if we have nodes
    if (nodes.length > 0) {
      try {
        graph.on('node:click', evt => {
          const id = evt.item.getID();
          setSelected(id);
          setDetailOpen(true);
        });

        graph.data({ nodes, edges });
        graph.render();
        graphRef.current = graph;
      } catch (err) {
        console.error("G6 Render Error:", err);
        messageApi.error("图谱渲染失败，请刷新重试");
      }
    }

    };

    render().catch((err) => {
      // 分块下载失败（弱网、被拦截）时给出可理解的提示，别留白屏
      console.error('G6 Load Error:', err);
      messageApi.error('图谱组件加载失败，请刷新重试');
    });

    return () => {
      cancelled = true;
      if (graphRef.current) {
        try {
          graphRef.current.destroy(); 
        } catch {}
        graphRef.current = null; 
      } 
    };
  }, [graphData]);

  const onSearch = async (values) => {
    setLoading(true);
    try {
      setTimeoutHint(false);
      setLastParams({ subject: values.subject, topic: values.topic });
      if (controllerRef.current) controllerRef.current.abort();
      controllerRef.current = new AbortController();
      const token = localStorage.getItem('token');
      const resp = await fetch(`${API_URL}/agent/knowledge/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': `Bearer ${token}` } : {})
        },
        body: JSON.stringify({ subject: values.subject, topic: values.topic }),
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
      let nodesAcc = [];
      let edgesAcc = [];
      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split(/\r?\n\r?\n/);
        buffer = parts.pop();
        for (const evt of parts) {
          const line = evt.trim();
          if (!line.startsWith('data:')) continue;
          const data = line.slice(5).trim();
          if (data === '[DONE]') continue;
          if (!data) continue;
          try {
            const obj = JSON.parse(data);
            if (obj.type === 'status') {
               messageApi.open({
                 key: 'kg_status',
                 type: 'loading',
                 content: obj.content,
                 duration: 0,
               });
            } else if (obj.type === 'node' && obj.node) {
              nodesAcc.push(obj.node);
              // Don't update state immediately to prevent re-render storm
            } else if (obj.type === 'edge' && obj.edge) {
              edgesAcc.push(obj.edge);
            } else if (obj.type === 'graph' && obj.nodes && obj.edges) {
              nodesAcc = obj.nodes;
              edgesAcc = obj.edges;
              setGraphData({ nodes: nodesAcc, edges: edgesAcc });
              messageApi.success(`图谱生成完成，共 ${nodesAcc.length} 个节点`);
            }
          } catch (e) {
            console.warn('Parse error', e, data);
          }
        }
      }
      messageApi.destroy('kg_status');
      // Final update if we didn't get a 'graph' event but collected nodes
      if (nodesAcc.length > 0 && (!graphData || graphData.nodes.length !== nodesAcc.length)) {
          setGraphData({ nodes: nodesAcc, edges: edgesAcc });
      }
      
      if (!nodesAcc.length) {
        const response = await agentService.getKnowledgeGraph(values.subject, values.topic, { signal: controllerRef.current.signal, timeout: 45000 });
        setGraphData(response.data);
        setLastGraph(response.data);
        try { localStorage.setItem('kg:lastGraph', JSON.stringify(response.data)); } catch {}
      } else {
        const final = { nodes: nodesAcc, edges: edgesAcc };
        setLastGraph(final);
        try { localStorage.setItem('kg:lastGraph', JSON.stringify(final)); } catch {}
      }
    } catch (error) {
      const name = error?.name || '';
      const msg = error?.message || '';
      const code = error?.code || '';
      if (name === 'CanceledError' || String(msg).includes('canceled') || code === 'ERR_CANCELED') return;
      if (code === 'ECONNABORTED') {
        messageApi.error('生成图谱请求超时，请稍后重试。');
        setTimeoutHint(true);
        return;
      }
      const detail = error?.response?.data?.detail;
      messageApi.error(detail ? `生成图谱失败：${detail}` : '生成图谱失败');
      console.error(error);
    } finally {
      setLoading(false);
      controllerRef.current = null;
    }
  };

  useEffect(() => {
    try {
      const raw = localStorage.getItem('kg:lastGraph');
      if (raw) {
        const data = JSON.parse(raw);
        setLastGraph(data);
      }
    } catch {}
  }, []);

  const retryLast = async () => {
    if (!lastParams) return;
    await onSearch(lastParams);
  };

  const showCached = () => {
    if (lastGraph) {
      setGraphData(lastGraph);
      setTimeoutHint(false);
    } else {
      messageApi.info('暂无可用的缓存图谱');
    }
  };

  return (
    <div>
      {contextHolder}
      <Title level={3}>知识图谱探索</Title>
      <Card style={{ marginBottom: 24 }}>
        <Form layout="inline" onFinish={onSearch}>
          <Form.Item name="subject" rules={[{ required: true }]}>
            <Input placeholder="学科 (例如：物理)" />
          </Form.Item>
          <Form.Item name="topic" rules={[{ required: true }]}>
            <Input placeholder="主题 (例如：牛顿定律)" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" icon={<SearchOutlined />} loading={loading}>
              生成图谱
            </Button>
          </Form.Item>
        </Form>
      </Card>
      {timeoutHint && (
        <Alert
          message="生成图谱请求超时"
          description={
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <Button type="primary" onClick={retryLast} disabled={loading}>重试</Button>
              <Button onClick={showCached} disabled={loading || !lastGraph}>显示上一次图谱</Button>
            </div>
          }
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      {graphData ? (
        <Card title={`知识图谱：${graphData.nodes ? graphData.nodes.length + ' 个概念' : ''}`}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
            {graphData.nodes && graphData.nodes.map((node, idx) => (
              <Card key={`card-${String(node.id)}-${idx}`} size="small" title={node.label} style={{ width: 300 }}>
                <p>{node.description}</p>
              </Card>
            ))}
          </div>

          <Title level={4} style={{ marginTop: 24 }}>知识关联</Title>
          <div style={{ marginTop: 16 }}>
            {graphData.edges && graphData.edges.map((edge, idx) => {
              const source = graphData.nodes.find(n => n.id === edge.source)?.label || edge.source;
              const target = graphData.nodes.find(n => n.id === edge.target)?.label || edge.target;
              return (
                <Tag key={`edge-${String(edge.source)}-${String(edge.target)}-${idx}`} color="blue" style={{ marginBottom: 8, padding: 8, fontSize: 14 }}>
                  {source} ➡️ <strong>{edge.relationship}</strong> ➡️ {target}
                </Tag>
              );
            })}
          </div>

          <Title level={4} style={{ marginTop: 24 }}>交互式图视图</Title>
          <div ref={g6Ref} style={{ height: 520, border: '1px solid var(--glass-border)', borderRadius: 8, background: 'rgba(255,255,255,0.6)', marginBottom: 16 }} />
          <SVGGraph data={graphData} selected={selected} onSelect={(id) => { setSelected(id); setDetailOpen(true); }} />
          <Modal
            open={detailOpen}
            onCancel={() => setDetailOpen(false)}
            footer={null}
            title={(graphData?.nodes || []).find(n => String(n.id) === String(selected))?.label || '节点详情'}
          >
            {(() => {
              const node = (graphData?.nodes || []).find(n => String(n.id) === String(selected));
              if (!node) return null;
              const neighbors = (graphData?.edges || []).filter(e => String(e.source) === String(node.id) || String(e.target) === String(node.id));
              return (
                <div>
                  <p style={{ marginBottom: 8 }}>{node.description}</p>
                  {neighbors.length > 0 && (
                    <div>
                      <Title level={5} style={{ marginTop: 12 }}>关联</Title>
                      {neighbors.map((e, i) => {
                        const s = (graphData?.nodes || []).find(n => String(n.id) === String(e.source))?.label || e.source;
                        const t = (graphData?.nodes || []).find(n => String(n.id) === String(e.target))?.label || e.target;
                        return (
                          <Tag key={`rel-${i}`} color="processing" style={{ marginBottom: 8 }}>
                            {s} ➡️ {e.relationship} ➡️ {t}
                          </Tag>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })()}
          </Modal>
        </Card>
      ) : (
        <Empty description="输入学科和主题以生成知识图谱" />
      )}
    </div>
  );
};

const SVGGraph = ({ data, selected, onSelect }) => {
  const size = 600;
  const radius = 250;
  const nodes = data?.nodes || [];
  const edges = data?.edges || [];
  const [hovered, setHovered] = useState(null);
  const positions = useMemo(() => {
    const map = {};
    const count = nodes.length || 1;
    nodes.forEach((n, i) => {
      const angle = (2 * Math.PI * i) / count;
      map[n.id] = {
        x: size / 2 + radius * Math.cos(angle),
        y: size / 2 + radius * Math.sin(angle),
      };
    });
    return map;
  }, [nodes]);
  const neighbors = useMemo(() => {
    const set = new Set();
    const base = hovered || selected;
    if (base) {
      edges.forEach(e => {
        if (String(e.source) === String(base)) set.add(String(e.target));
        if (String(e.target) === String(base)) set.add(String(e.source));
      });
    }
    return set;
  }, [hovered, selected, edges]);
  return (
    <div style={{ overflow: 'auto', border: '1px solid var(--glass-border)', borderRadius: 8 }}>
      <svg width={size} height={size} style={{ background: 'rgba(255,255,255,0.6)' }}>
        {edges.map((e, i) => {
          const s = positions[e.source];
          const t = positions[e.target];
          if (!s || !t) return null;
          const base = hovered || selected;
          const active = String(base) === String(e.source) || String(base) === String(e.target) || neighbors.has(String(e.source)) || neighbors.has(String(e.target));
          return (
          <g key={`e-${i}`}>
            <line x1={s.x} y1={s.y} x2={t.x} y2={t.y} stroke={active ? '#69b1ff' : '#999'} strokeWidth={active ? 2 : 1} />
          </g>
          );
        })}
        {nodes.map((n) => {
          const p = positions[n.id];
          if (!p) return null;
          const base = hovered || selected;
          const active = String(base) === String(n.id) || neighbors.has(String(n.id));
          return (
            <g key={`n-${n.id}`} onClick={() => onSelect(n.id)} onMouseEnter={() => setHovered(n.id)} onMouseLeave={() => setHovered(null)} style={{ cursor: 'pointer' }}>
              <circle cx={p.x} cy={p.y} r={active ? 18 : 14} fill={active ? '#1677ff' : '#555'} />
              <text x={p.x} y={p.y - (active ? 24 : 20)} fill="#fff" fontSize="12" textAnchor="middle">{n.label}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
};


export default KnowledgeGraph;
