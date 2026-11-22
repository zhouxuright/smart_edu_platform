import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Empty, List, Modal, Popconfirm, Progress, Tag, Tooltip, Upload, message,
} from 'antd';
import {
  Download, Eye, FileText, Loader2, Paperclip, Sparkles, Trash2, UploadCloud,
} from 'lucide-react';
import { resumeService } from '../services/api';
import { track } from '../services/telemetry';
import './ResumeAttachments.css';

const { Dragger } = Upload;

const MAX_BATCH_FILES = 10;
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const MAX_BATCH_BYTES = 30 * 1024 * 1024;
const ACCEPT = '.pdf,.docx,.doc,.txt,.md';

const STATUS_TEXT = { ok: '已上传', duplicated: '重复跳过', failed: '失败' };

function humanSize(bytes = 0) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fileIcon() {
  return <FileText size={16} aria-hidden="true" />;
}

/**
 * 按「最多 10 个 / 合计 30MB」切片。
 * 一次选 25 个文件时，前端自己拆成 3 批顺序发，用户不必再手动分次拖。
 */
function chunkFiles(files) {
  const chunks = [];
  let current = [];
  let bytes = 0;
  files.forEach((file) => {
    const size = file.size || 0;
    const overflow = current.length >= MAX_BATCH_FILES
      || (current.length > 0 && bytes + size > MAX_BATCH_BYTES);
    if (overflow) {
      chunks.push(current);
      current = [];
      bytes = 0;
    }
    current.push(file);
    bytes += size;
  });
  if (current.length) chunks.push(current);
  return chunks;
}

export default function ResumeAttachments({ resumeId, onImported, onUploaded }) {
  const [items, setItems] = useState([]);
  const [limits, setLimits] = useState({ limit: 20, max_batch_files: MAX_BATCH_FILES });
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [batchReport, setBatchReport] = useState(null);

  const [previewOpen, setPreviewOpen] = useState(false);
  const [preview, setPreview] = useState({ filename: '', text: '', loading: false, error: '' });

  const [importOpen, setImportOpen] = useState(false);
  const [importBusy, setImportBusy] = useState('');
  const [pending, setPending] = useState(null);

  const queueRef = useRef([]);
  const timerRef = useRef(null);
  const mountedRef = useRef(true);

  useEffect(() => () => {
    mountedRef.current = false;
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  const refresh = useCallback(async (id = resumeId) => {
    if (!id) return;
    try {
      const res = await resumeService.attachments(id);
      if (!mountedRef.current) return;
      setItems(res.data?.items || []);
      setLimits({
        limit: res.data?.limit ?? 20,
        max_batch_files: res.data?.max_batch_files ?? MAX_BATCH_FILES,
        max_file_bytes: res.data?.max_file_bytes ?? MAX_FILE_BYTES,
      });
    } catch {
      if (mountedRef.current) message.error('附件列表加载失败');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [resumeId]);

  useEffect(() => {
    setLoading(true);
    setBatchReport(null);
    refresh(resumeId);
  }, [refresh, resumeId]);

  const flush = useCallback(async () => {
    const batch = queueRef.current.splice(0);
    if (!batch.length || !resumeId) return;
    setUploading(true);
    setProgress(0);
    setBatchReport(null);
    const collected = [];
    try {
      const chunks = chunkFiles(batch);
      for (let i = 0; i < chunks.length; i += 1) {
        const chunk = chunks[i];
        // 单批进度按「第几批 + 批内百分比」折算，多批时进度条不会反复回零
        const res = await resumeService.uploadAttachments(resumeId, chunk, (percent) => {
          if (mountedRef.current) {
            setProgress(Math.round(((i + percent / 100) / chunks.length) * 100));
          }
        });
        collected.push(...(res.data?.items || []));
      }
      if (!mountedRef.current) return;
      setProgress(100);
      setBatchReport(collected);
      const ok = collected.filter((r) => r.status === 'ok').length;
      const dup = collected.filter((r) => r.status === 'duplicated').length;
      const failed = collected.filter((r) => r.status === 'failed').length;
      if (ok) {
        message.success(`已上传 ${ok} 个附件${dup ? `，${dup} 个重复已跳过` : ''}`);
        track('resume_attachment_upload', { ok, dup, failed });
      } else if (dup && !failed) {
        message.info('这些文件之前已经传过了，无需重复上传');
      }
      await refresh(resumeId);
      onUploaded?.(collected);
    } catch (error) {
      if (!mountedRef.current) return;
      const detail = error?.response?.data?.detail;
      message.error(typeof detail === 'string' ? detail : '上传失败，请检查网络后重试');
    } finally {
      if (mountedRef.current) {
        setUploading(false);
        setTimeout(() => mountedRef.current && setProgress(0), 1200);
      }
    }
  }, [refresh, resumeId, onUploaded]);

  /** 选完文件先入队，400ms 内的一起发——多选/多拖只产生一次请求 */
  const enqueue = useCallback((file) => {
    if (!resumeId) {
      message.warning('请先等简历加载完成');
      return Upload.LIST_IGNORE;
    }
    queueRef.current.push(file);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => { flush(); }, 400);
    return Upload.LIST_IGNORE;
  }, [flush, resumeId]);

  const beforeUpload = useCallback((file) => {
    const name = (file.name || '').toLowerCase();
    const ext = name.includes('.') ? `.${name.split('.').pop()}` : '';
    if (!ACCEPT.split(',').includes(ext)) {
      message.error(`${file.name} 格式不支持，请上传 PDF / DOCX / DOC / TXT / MD`);
      return Upload.LIST_IGNORE;
    }
    if ((file.size || 0) > MAX_FILE_BYTES) {
      message.error(`${file.name} 超过 10MB`);
      return Upload.LIST_IGNORE;
    }
    if (items.length + queueRef.current.length >= limits.limit) {
      message.warning(`一份简历最多挂 ${limits.limit} 个附件，请先删掉不用的`);
      return Upload.LIST_IGNORE;
    }
    return enqueue(file);
  }, [enqueue, items.length, limits.limit]);

  async function openPreview(item) {
    setPreviewOpen(true);
    setPreview({ filename: item.filename, text: '', loading: true, error: '' });
    try {
      const res = await resumeService.attachmentText(resumeId, item.id);
      setPreview({
        filename: res.data?.filename || item.filename,
        text: res.data?.text || '',
        loading: false,
        error: res.data?.parse_error || (res.data?.text ? '' : '这份文件里没有可提取的文字'),
      });
    } catch {
      setPreview({ filename: item.filename, text: '', loading: false, error: '读取失败，请重试' });
    }
  }

  async function download(item) {
    try {
      const res = await resumeService.downloadAttachment(resumeId, item.id);
      const url = URL.createObjectURL(new Blob([res.data], { type: item.mime || 'application/octet-stream' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = item.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch {
      message.error('下载失败，请重试');
    }
  }

  async function remove(item) {
    try {
      await resumeService.removeAttachment(resumeId, item.id);
      setItems((prev) => prev.filter((x) => x.id !== item.id));
      setBatchReport(null);
      message.success('已删除');
    } catch {
      message.error('删除失败');
    }
  }

  async function startImport(item) {
    setImportBusy(item.id);
    try {
      const res = await resumeService.importAttachment(resumeId, item.id, false);
      setPending({ item, data: res.data });
      setImportOpen(true);
    } catch (error) {
      const status = error?.response?.status;
      const detail = error?.response?.data?.detail;
      if (status === 429) message.error('今日 AI 额度已用完');
      else if (status === 400 || status === 422) message.warning(typeof detail === 'string' ? detail : '无法解析这份附件');
      else message.error('AI 解析暂时不可用，请稍后再试');
    } finally {
      setImportBusy('');
    }
  }

  async function confirmImport() {
    if (!pending) return;
    setImportBusy(`apply-${pending.item.id}`);
    try {
      const res = await resumeService.importAttachment(resumeId, pending.item.id, true);
      setImportOpen(false);
      setPending(null);
      track('resume_attachment_import');
      message.success('已导入，左侧内容已更新（导入前已自动留档）');
      onImported?.(res.data);
    } catch (error) {
      const status = error?.response?.status;
      message.error(status === 429 ? '今日 AI 额度已用完' : '导入失败，请重试');
    } finally {
      setImportBusy('');
    }
  }

  const summary = useMemo(() => {
    if (!batchReport?.length) return null;
    const failed = batchReport.filter((r) => r.status === 'failed');
    const dup = batchReport.filter((r) => r.status === 'duplicated');
    if (!failed.length && !dup.length) return null;
    return { failed, dup };
  }, [batchReport]);

  return (
    <section className="resume-attach" aria-label="附件简历">
      <div className="resume-attach-head">
        <span className="resume-attach-title"><Paperclip size={15} aria-hidden="true" /> 附件简历</span>
        <span className="resume-attach-hint">
          {items.length}/{limits.limit} · PDF/DOCX/DOC/TXT/MD · 单文件 10MB
        </span>
      </div>

      <Dragger
        multiple
        accept={ACCEPT}
        beforeUpload={beforeUpload}
        showUploadList={false}
        disabled={!resumeId || uploading}
        className="resume-attach-drop"
      >
        <p className="resume-attach-drop-icon">
          {uploading ? <Loader2 size={22} className="resume-attach-spin" /> : <UploadCloud size={22} />}
        </p>
        <p className="resume-attach-drop-main">
          {uploading ? '正在上传…' : '拖文件到这里，或点击选择'}
        </p>
        <p className="resume-attach-drop-sub">
          支持一次选多个文件（最多 10 个 / 30MB），也可以直接拖入一整批
        </p>
      </Dragger>

      {uploading && <Progress percent={progress} size="small" status="active" strokeColor="#4a6cf7" />}

      {summary && (
        <Alert
          type="warning"
          showIcon
          closable
          onClose={() => setBatchReport(null)}
          className="resume-attach-alert"
          message={`${summary.failed.length + summary.dup.length} 个文件未入库`}
          description={(
            <ul className="resume-attach-alert-list">
              {[...summary.failed, ...summary.dup].map((r, i) => (
                <li key={`${r.filename}-${i}`}>
                  <strong>{r.filename}</strong>
                  <span>{r.error || STATUS_TEXT[r.status]}</span>
                </li>
              ))}
            </ul>
          )}
        />
      )}

      {loading ? (
        <p className="resume-attach-empty">附件加载中…</p>
      ) : items.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="还没有附件。把已有的简历传上来，可以在这里预览文字、下载原件，也能让 AI 解析成结构化内容。"
        />
      ) : (
        <List
          className="resume-attach-list"
          dataSource={items}
          renderItem={(item) => (
            <List.Item
              key={item.id}
              actions={[
                <Tooltip key="eye" title={item.has_text ? '预览提取的文字' : '无可提取文字'}>
                  <Button
                    size="small"
                    type="text"
                    icon={<Eye size={14} />}
                    disabled={!item.has_text}
                    onClick={() => openPreview(item)}
                  />
                </Tooltip>,
                <Tooltip key="dl" title="下载原件">
                  <Button size="small" type="text" icon={<Download size={14} />} onClick={() => download(item)} />
                </Tooltip>,
                <Tooltip key="imp" title={item.has_text ? 'AI 解析成结构化简历' : '这份文件无法解析文字'}>
                  <Button
                    size="small"
                    type="text"
                    icon={<Sparkles size={14} />}
                    disabled={!item.has_text}
                    loading={importBusy === item.id}
                    onClick={() => startImport(item)}
                  />
                </Tooltip>,
                <Popconfirm
                  key="del"
                  title="删除这个附件？"
                  description="文件会从服务器一并删除"
                  okText="删除"
                  cancelText="取消"
                  onConfirm={() => remove(item)}
                >
                  <Button size="small" type="text" danger icon={<Trash2 size={14} />} />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                avatar={<span className="resume-attach-fileicon">{fileIcon()}</span>}
                title={<span className="resume-attach-name">{item.filename}</span>}
                description={(
                  <span className="resume-attach-meta">
                    <span className="resume-attach-meta-item">{humanSize(item.size_bytes)}</span>
                    {item.pages ? <span className="resume-attach-meta-item">{item.pages} 页</span> : null}
                    {item.chars ? <span className="resume-attach-meta-item">{item.chars} 字</span> : null}
                    {item.created_at ? (
                      <span className="resume-attach-meta-item">
                        {new Date(item.created_at).toLocaleDateString('zh-CN')}
                      </span>
                    ) : null}
                    {item.parse_error && <Tag color="orange" className="resume-attach-tag">{item.parse_error}</Tag>}
                  </span>
                )}
              />
            </List.Item>
          )}
        />
      )}

      <Modal
        title={preview.filename}
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={<Button onClick={() => setPreviewOpen(false)}>关闭</Button>}
        width={720}
      >
        {preview.loading ? (
          <p>读取中…</p>
        ) : preview.error && !preview.text ? (
          <Alert type="warning" showIcon message={preview.error} />
        ) : (
          <>
            {preview.error && <Alert type="warning" showIcon message={preview.error} style={{ marginBottom: 8 }} />}
            <pre className="resume-attach-preview">{preview.text}</pre>
          </>
        )}
      </Modal>

      <Modal
        title="用附件内容覆盖当前简历？"
        open={importOpen}
        onCancel={() => { setImportOpen(false); setPending(null); }}
        onOk={confirmImport}
        okText="确认导入"
        cancelText="取消"
        confirmLoading={Boolean(importBusy)}
      >
        {pending && (
          <div className="resume-attach-import">
            <p>
              从 <strong>{pending.item.filename}</strong> 解析出：
            </p>
            <ul>
              <li>姓名：{pending.data?.data?.basics?.name || '未识别'}</li>
              <li>
                教育 {pending.data?.data?.education?.length || 0} 段 ·
                实习 {pending.data?.data?.experience?.length || 0} 段 ·
                项目 {pending.data?.data?.projects?.length || 0} 段 ·
                技能 {pending.data?.data?.skills?.length || 0} 项
              </li>
              <li>完整度：{pending.data?.completeness ?? 0}%</li>
            </ul>
            <Alert
              type="warning"
              showIcon
              message="会覆盖左侧已填的结构化内容"
              description="导入前会自动存一个版本，导入后如果不对，去「版本」里恢复即可。"
            />
          </div>
        )}
      </Modal>
    </section>
  );
}
