import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  Card, Form, Input, Button, Typography, List, Spin, message, Upload, Tag, Space, Empty, Popconfirm,
} from 'antd';
import { InboxOutlined, FileTextOutlined, DeleteOutlined, SyncOutlined, DatabaseOutlined } from '@ant-design/icons';
import api from '../services/api';
import EmptyGuide from '../components/EmptyGuide';
import { track } from '../services/telemetry';

const STATUS_META = {
  ok: { color: 'green', text: '已入库' },
  failed: { color: 'red', text: '失败' },
  duplicated: { color: 'orange', text: '已存在' },
};

const EnterpriseRAG = () => {
  const [messageApi, contextHolder] = message.useMessage();
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState([]);
  const [searchMode, setSearchMode] = useState('');
  const [fileList, setFileList] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [fileResult, setFileResult] = useState(null);
  const [textResult, setTextResult] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const searchControllerRef = useRef(null);

  const loadDocuments = useCallback(async () => {
    setDocsLoading(true);
    try {
      const res = await api.get('/rag/documents', { params: { limit: 100 } });
      setDocuments(res.data.documents || []);
    } catch {
      messageApi.error('加载文档列表失败');
    } finally {
      setDocsLoading(false);
    }
  }, [messageApi]);

  useEffect(() => {
    loadDocuments();
    return () => { searchControllerRef.current?.abort(); };
  }, [loadDocuments]);

  const onBatchUpload = async () => {
    const files = fileList.map((item) => item.originFileObj).filter(Boolean);
    if (!files.length) {
      messageApi.error('请先选择文件');
      return;
    }
    setUploading(true);
    setFileResult(null);
    try {
      const fd = new FormData();
      files.forEach((file) => fd.append('files', file));
      const res = await api.post('/rag/documents/batch', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 180000,
      });
      const data = res.data;
      setFileResult(data);
      if (data.failed === 0 && data.duplicated === 0) {
        messageApi.success(`${data.succeeded} 个文件全部解析入库`);
      } else {
        messageApi.warning(`成功 ${data.succeeded} 个，失败 ${data.failed} 个，跳过 ${data.duplicated} 个`);
      }
      setFileList([]);
      await loadDocuments();
    } catch (e) {
      const detail = e?.response?.data?.detail;
      messageApi.error(detail ? `批量上传失败：${detail}` : '批量上传失败');
    } finally {
      setUploading(false);
    }
  };

  const onBatchText = async (values) => {
    const raw = (values.documents || '').trim();
    if (!raw) {
      messageApi.error('请粘贴内容');
      return;
    }
    const blocks = raw.split(/\n-{3,}\n/).map((item) => item.trim()).filter(Boolean);
    const items = blocks.map((block, index) => {
      const firstLine = block.split('\n')[0].trim();
      const hasTitle = firstLine.startsWith('#');
      return {
        title: hasTitle ? firstLine.replace(/^#+\s*/, '').slice(0, 60) : `粘贴文档 ${index + 1}`,
        content: hasTitle ? block.split('\n').slice(1).join('\n').trim() : block,
      };
    });
    if (!items.length) {
      messageApi.error('没有解析出内容');
      return;
    }
    setUploading(true);
    setTextResult(null);
    try {
      const res = await api.post('/rag/documents/batch-text', { items }, { timeout: 180000 });
      setTextResult(res.data);
      messageApi.success(`成功 ${res.data.succeeded} 篇，失败 ${res.data.failed} 篇`);
      await loadDocuments();
    } catch (e) {
      const detail = e?.response?.data?.detail;
      messageApi.error(detail ? `批量入库失败：${detail}` : '批量入库失败');
    } finally {
      setUploading(false);
    }
  };

  const onSearch = async (values) => {
    setSearching(true);
    try {
      searchControllerRef.current?.abort();
      searchControllerRef.current = new AbortController();
      const res = await api.post(
        '/rag/search',
        { query: values.query, top_k: 5 },
        { signal: searchControllerRef.current.signal },
      );
      setResults(res.data.results || []);
      setSearchMode(res.data.mode || '');
      track('rag_search', { hits: (res.data.results || []).length, mode: res.data.mode || '' });
      if (!res.data.results?.length) messageApi.info('没有检索到相关内容，先确认文档已入库');
    } catch (e) {
      if (e?.name === 'CanceledError') return;
      const detail = e?.response?.data?.detail;
      messageApi.error(detail ? `检索失败：${detail}` : '检索失败');
    } finally {
      setSearching(false);
    }
  };

  const onDelete = async (id) => {
    try {
      await api.delete(`/rag/documents/${id}`);
      messageApi.success('已删除');
      await loadDocuments();
    } catch {
      messageApi.error('删除失败');
    }
  };

  const onReindex = async (id) => {
    try {
      const res = await api.post(`/rag/documents/${id}/reindex`);
      messageApi.success(`已重建 ${res.data.chunks} 个分块的向量`);
    } catch (e) {
      messageApi.error(e?.response?.data?.detail || '重建失败');
    }
  };

  const renderUploadResult = (uploadResult) => {
    if (!uploadResult) return null;
    return (
      <div style={{ marginTop: 16 }}>
        <Space size={12} style={{ marginBottom: 8 }}>
          <Tag color="green">成功 {uploadResult.succeeded}</Tag>
          <Tag color="red">失败 {uploadResult.failed}</Tag>
          <Tag color="orange">跳过 {uploadResult.duplicated}</Tag>
        </Space>
        <List
          size="small"
          dataSource={uploadResult.results || []}
          renderItem={(item) => {
            const meta = STATUS_META[item.status] || STATUS_META.failed;
            const name = item.filename || item.title;
            return (
              <List.Item>
                <Space size={8} wrap>
                  <Tag color={meta.color}>{meta.text}</Tag>
                  <span style={{ fontWeight: 500 }}>{name}</span>
                  {item.status === 'ok' && (
                    <span style={{ opacity: 0.7, fontSize: 12 }}>
                      {item.chars} 字 · {item.chunks} 块{item.pages ? ` · ${item.pages} 页` : ''}
                    </span>
                  )}
                  {item.error && <span style={{ fontSize: 12, color: '#a32d2d' }}>{item.error}</span>}
                  {item.warnings?.length > 0 && (
                    <span style={{ fontSize: 12, color: '#854f0b' }}>{item.warnings.join('；')}</span>
                  )}
                </Space>
              </List.Item>
            );
          }}
        />
      </div>
    );
  };

  return (
    <div>
      {contextHolder}
      <Typography.Title level={3}>知识库（RAG）</Typography.Title>
      <Typography.Paragraph style={{ opacity: 0.75 }}>
        支持 pdf / docx / txt / md / csv / json。一次最多 20 个文件、单文件 20MB、总计 60MB。
        同名文档会自动跳过，失败的文件不影响其他文件。
      </Typography.Paragraph>

      <Card title="批量上传文件" style={{ marginBottom: 24 }}>
        <Upload.Dragger
          multiple
          accept=".pdf,.docx,.txt,.md,.markdown,.csv,.json,.log"
          beforeUpload={() => false}
          fileList={fileList}
          onChange={({ fileList: next }) => setFileList(next)}
          disabled={uploading}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">点击或拖拽文件到这里，可一次选多个</p>
          <p className="ant-upload-hint">旧版 .doc 请先另存为 .docx；扫描版 PDF 需要先 OCR</p>
        </Upload.Dragger>
        <div style={{ marginTop: 16 }}>
          <Button type="primary" onClick={onBatchUpload} loading={uploading} disabled={!fileList.length}>
            开始解析入库（{fileList.length} 个）
          </Button>
        </div>
        {renderUploadResult(fileResult)}
      </Card>

      <Card title="批量粘贴文本" style={{ marginBottom: 24 }}>
        <Form layout="vertical" onFinish={onBatchText}>
          <Form.Item
            name="documents"
            label="多篇文档用单独一行 --- 分隔；每篇首行以 # 开头可作为标题"
            rules={[{ required: true, message: '请粘贴内容' }]}
          >
            <Input.TextArea
              rows={8}
              placeholder={'# 简历撰写要点\n一页纸、量化成果、匹配岗位关键词……\n---\n# 面试复盘模板\n每个问题都要写下当时的回答与更好的版本……'}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={uploading}>解析入库</Button>
        </Form>
        {renderUploadResult(textResult)}
      </Card>

      <Card
        title="已入库文档"
        style={{ marginBottom: 24 }}
        extra={<Button size="small" icon={<SyncOutlined />} onClick={loadDocuments} loading={docsLoading}>刷新</Button>}
      >
        {documents.length === 0 ? (
          <EmptyGuide
            compact
            icon={<DatabaseOutlined />}
            title="知识库还是空的"
            description="上传课程资料、笔记或岗位说明，系统会自动切块并建立语义索引。之后提问时就能带着原文段落回答，而不是凭空生成。"
            action={
              <Button type="primary" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
                去上传文档
              </Button>
            }
          />
        ) : (
          <List
            dataSource={documents}
            renderItem={(doc) => (
              <List.Item
                actions={[
                  <Button key="reindex" type="link" size="small" onClick={() => onReindex(doc.id)}>重建向量</Button>,
                  <Popconfirm key="del" title="删除这篇文档及其分块？" onConfirm={() => onDelete(doc.id)}>
                    <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  avatar={<FileTextOutlined />}
                  title={doc.title}
                  description={`${doc.chars} 字 · ${doc.chunks} 个分块${doc.chunks === 0 ? '（无分块，建议删除重传）' : ''}`}
                />
              </List.Item>
            )}
          />
        )}
      </Card>

      <Card title="知识检索">
        <Form layout="inline" onFinish={onSearch}>
          <Form.Item name="query" rules={[{ required: true, message: '请输入检索问题' }]}>
            <Input placeholder="输入检索问题（中文）" style={{ width: 400 }} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={searching}>立即搜索</Button>
          </Form.Item>
        </Form>

        {searching ? (
          <div style={{ textAlign: 'center', padding: 50 }}><Spin /></div>
        ) : results.length === 0 ? (
          <Empty style={{ marginTop: 24 }} description="暂无检索结果" />
        ) : (
          <List
            style={{ marginTop: 16 }}
            dataSource={results}
            renderItem={(item) => (
              <List.Item>
                <Card
                  size="small"
                  style={{ width: '100%' }}
                  title={
                    <Space size={8} wrap>
                      <span>{item.title}</span>
                      <Tag color="blue">匹配度 {Math.round((item.score || 0) * 100)}%</Tag>
                      {typeof item.semantic_score === 'number' && (
                        <Tag color="geekblue">语义 {Math.round(item.semantic_score * 100)}%</Tag>
                      )}
                      {typeof item.keyword_score === 'number' && (
                        <Tag color="gold">关键词 {Math.round(item.keyword_score * 100)}%</Tag>
                      )}
                      {item.chunk_seq !== undefined && <Tag>第 {item.chunk_seq + 1} 块</Tag>}
                    </Space>
                  }
                >
                  <div style={{ whiteSpace: 'pre-wrap' }}>{item.content}</div>
                  {item.url && (
                    <div style={{ marginTop: 8 }}>
                      来源：<a href={item.url} target="_blank" rel="noreferrer">{item.url}</a>
                    </div>
                  )}
                </Card>
              </List.Item>
            )}
          />
        )}
        {searchMode === 'chunk' && (
          <Typography.Paragraph style={{ marginTop: 12, fontSize: 12, opacity: 0.6 }}>
            当前为分块级混合检索：BM25 关键词分 + BGE-M3 语义向量分加权融合，再做 MMR 去重。
            匹配度是融合分的单调映射，语义/关键词两栏说明该结果主要靠哪一路命中。
          </Typography.Paragraph>
        )}
      </Card>
    </div>
  );
};

export default EnterpriseRAG;
