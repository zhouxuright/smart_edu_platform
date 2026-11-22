import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Button, Card, Col, Drawer, Empty, Input, List, message, Popconfirm, Progress,
  Row, Select, Space, Tag, Tooltip, Typography,
} from 'antd';
import {
  ArrowDown, ArrowUp, Download, FileText, History, Plus, Save, Sparkles, Trash2,
} from 'lucide-react';
import { resumeService } from '../services/api';
import { track } from '../services/telemetry';
import { useAgentApplied, useAgentPageId } from '../services/agentPage';
import ResumeAttachments from '../components/ResumeAttachments';
import './ResumeStudio.css';

const { TextArea } = Input;

const blankData = () => ({
  basics: { name: '', phone: '', email: '', city: '', github: '', summary: '' },
  education: [],
  experience: [],
  projects: [],
  skills: [],
  awards: [],
});

const EDU_TEMPLATE = { school: '', major: '', degree: '本科', start: '', end: '', gpa: '', highlights: [] };
const EXP_TEMPLATE = { company: '', role: '', start: '', end: '', bullets: [] };
const PROJ_TEMPLATE = { name: '', role: '', start: '', end: '', bullets: [] };

/** 数组字段的通用增删改：所有板块共用一套，避免五份重复实现 */
function useListOps(setData, key) {
  return {
    add: (template) => setData((prev) => ({ ...prev, [key]: [...prev[key], { ...template }] })),
    remove: (index) => setData((prev) => ({ ...prev, [key]: prev[key].filter((_, i) => i !== index) })),
    patch: (index, changes) => setData((prev) => ({
      ...prev,
      [key]: prev[key].map((item, i) => (i === index ? { ...item, ...changes } : item)),
    })),
    move: (index, dir) => setData((prev) => {
      const next = [...prev[key]];
      const target = index + dir;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return { ...prev, [key]: next };
    }),
  };
}

function SectionCard({ icon, title, extra, children }) {
  return (
    <Card
      size="small"
      className="resume-section"
      title={<span className="resume-section-title">{icon}{title}</span>}
      extra={extra}
    >
      {children}
    </Card>
  );
}

function EntryToolbar({ index, total, onMove, onRemove }) {
  return (
    <Space size={2}>
      <Tooltip title="上移">
        <Button size="small" type="text" disabled={index === 0} onClick={() => onMove(index, -1)} icon={<ArrowUp size={14} />} />
      </Tooltip>
      <Tooltip title="下移">
        <Button size="small" type="text" disabled={index === total - 1} onClick={() => onMove(index, 1)} icon={<ArrowDown size={14} />} />
      </Tooltip>
      <Popconfirm title="删除这一条？" okText="删除" cancelText="取消" onConfirm={() => onRemove(index)}>
        <Button size="small" type="text" danger icon={<Trash2 size={14} />} />
      </Popconfirm>
    </Space>
  );
}

/** 要点输入：一行一条，比一堆单行输入框更好用 */
function BulletInput({ value = [], onChange, placeholder }) {
  return (
    <TextArea
      value={(value || []).join('\n')}
      onChange={(e) => onChange(e.target.value.split('\n'))}
      autoSize={{ minRows: 2, maxRows: 8 }}
      placeholder={placeholder || '一行一条，例如：负责会员中心竞品分析，输出 3 份对比文档'}
    />
  );
}

export default function ResumeStudio() {
  const [list, setList] = useState([]);
  const [current, setCurrent] = useState(null);
  const [data, setData] = useState(blankData);
  const [title, setTitle] = useState('');
  const [targetRole, setTargetRole] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [polishing, setPolishing] = useState('');

  const [versionOpen, setVersionOpen] = useState(false);
  const [versions, setVersions] = useState([]);
  const [versionLoading, setVersionLoading] = useState(false);

  const latest = useRef({});
  latest.current = { id: current?.id, title, targetRole, data };

  const edu = useListOps(setData, 'education');
  const exp = useListOps(setData, 'experience');
  const proj = useListOps(setData, 'projects');

  const applyResume = useCallback((resume) => {
    setCurrent(resume);
    setTitle(resume.title || '');
    setTargetRole(resume.target_role || '');
    setData({ ...blankData(), ...(resume.data || {}) });
    setDirty(false);
  }, []);

  // 告诉 Agent「现在编辑的是哪一份」，否则它会去改最近更新过的那份
  useAgentPageId(current?.id);

  // Agent 落库后广播，这里重新拉一次——否则界面上还是改之前的文字
  useAgentApplied(({ tool }) => {
    if (tool !== 'update_bullets' || !current?.id) return;
    resumeService
      .get(current.id)
      .then((res) => {
        applyResume(res.data);
        message.success('AI 改动已应用，页面已刷新');
      })
      .catch(() => message.error('刷新简历失败，请手动刷新页面'));
  });

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await resumeService.list();
        const items = res.data?.items || [];
        if (!alive) return;
        setList(items);
        if (items.length) {
          const detail = await resumeService.get(items[0].id);
          if (alive) applyResume(detail.data);
        } else {
          const created = await resumeService.create({ title: '我的第一份简历' });
          if (alive) {
            setList([created.data]);
            applyResume(created.data);
          }
        }
      } catch {
        if (alive) message.error('简历加载失败，请刷新重试');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [applyResume]);

  const persist = useCallback(async ({ snapshot = false, note, silent = false } = {}) => {
    if (!current?.id) return;
    setSaving(true);
    try {
      const res = await resumeService.update(
        current.id,
        { title: title || '未命名简历', target_role: targetRole || null, data },
        { snapshot, note },
      );
      setCurrent(res.data);
      setDirty(false);
      setList((prev) => prev.map((item) => (item.id === res.data.id
        ? { ...item, title: res.data.title, target_role: res.data.target_role, version: res.data.version, updated_at: res.data.updated_at }
        : item)));
      if (!silent) message.success(snapshot ? '已保存并留档' : '已保存');
    } catch {
      if (!silent) message.error('保存失败，请重试');
    } finally {
      setSaving(false);
    }
  }, [current?.id, title, targetRole, data]);

  // 自动保存：停手 1.5 秒落一次，避免用户辛苦填完忘了点保存
  useEffect(() => {
    if (!dirty || !current?.id) return undefined;
    const timer = setTimeout(() => {
      const snap = latest.current;
      resumeService.update(snap.id, { title: snap.title || '未命名简历', target_role: snap.targetRole || null, data: snap.data })
        .then(() => setDirty(false))
        .catch(() => {});
    }, 1500);
    return () => clearTimeout(timer);
  }, [dirty, data, title, targetRole, current?.id]);

  const markDirty = () => setDirty(true);

  async function switchResume(id) {
    if (dirty) await persist({ silent: true });
    try {
      const res = await resumeService.get(id);
      applyResume(res.data);
    } catch {
      message.error('切换失败');
    }
  }

  async function createResume() {
    if (dirty) await persist({ silent: true });
    try {
      const res = await resumeService.create({ title: `简历 ${list.length + 1}` });
      setList((prev) => [res.data, ...prev]);
      applyResume(res.data);
      track('resume_create');
    } catch {
      message.error('新建失败');
    }
  }

  async function removeResume() {
    if (!current?.id) return;
    try {
      await resumeService.remove(current.id);
      const rest = list.filter((item) => item.id !== current.id);
      setList(rest);
      if (rest.length) {
        const detail = await resumeService.get(rest[0].id);
        applyResume(detail.data);
      } else {
        const created = await resumeService.create({ title: '我的第一份简历' });
        setList([created.data]);
        applyResume(created.data);
      }
      message.success('已删除');
    } catch {
      message.error('删除失败');
    }
  }

  async function exportPdf() {
    if (!current?.id) return;
    setExporting(true);
    try {
      if (dirty) await persist({ silent: true });
      const res = await resumeService.exportPdf(current.id);
      const blob = new Blob([res.data], { type: 'application/pdf' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${data.basics?.name || '我的'}-简历.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      track('resume_export_pdf');
      message.success('PDF 已开始下载');
    } catch (error) {
      const status = error?.response?.status;
      message.error(status === 503 ? '服务器缺少中文字体，暂时无法导出' : '导出失败，请稍后重试');
    } finally {
      setExporting(false);
    }
  }

  async function openVersions() {
    setVersionOpen(true);
    if (!current?.id) return;
    setVersionLoading(true);
    try {
      const res = await resumeService.versions(current.id);
      setVersions(res.data?.items || []);
    } catch {
      message.error('版本列表加载失败');
    } finally {
      setVersionLoading(false);
    }
  }

  async function snapshotNow() {
    try {
      await persist({ silent: true });
      await resumeService.snapshot(current.id, '手动留档');
      message.success('已保存当前版本');
      openVersions();
    } catch {
      message.error('留档失败');
    }
  }

  async function restore(version) {
    try {
      const res = await resumeService.restore(current.id, version);
      applyResume(res.data);
      message.success(`已恢复到 v${version}`);
      openVersions();
    } catch {
      message.error('恢复失败');
    }
  }

  async function polish(section, item, index, key) {
    const raw = (item.bullets || []).join('\n').trim();
    if (raw.length < 8) {
      message.warning('先写几句原始描述，AI 才有内容可润色');
      return;
    }
    const token = `${section}-${index}`;
    setPolishing(token);
    try {
      const res = await resumeService.polish(current.id, { section, text: raw });
      const lines = res.data?.lines || [];
      if (!lines.length) throw new Error('empty');
      key.patch(index, { bullets: lines });
      markDirty();
      track('resume_polish');
      message.success('已改写，记得检查是否符合事实');
    } catch (error) {
      const status = error?.response?.status;
      if (status === 429) message.error('今日 AI 额度已用完');
      else message.error('AI 润色暂时不可用，请手动修改');
    } finally {
      setPolishing('');
    }
  }

  // 预览只画「有内容」的条目：刚点「添加」还没填的空行不该在纸上占一块
  const shown = useMemo(() => ({
    education: (data.education || []).filter((i) => i.school || i.major || (i.highlights || []).some(Boolean)),
    experience: (data.experience || []).filter((i) => i.company || i.role || (i.bullets || []).some(Boolean)),
    projects: (data.projects || []).filter((i) => i.name || i.role || (i.bullets || []).some(Boolean)),
  }), [data.education, data.experience, data.projects]);

  const completeness = useMemo(() => {
    const b = data.basics || {};
    const hasBullet = [...(data.education || []), ...(data.experience || []), ...(data.projects || [])]
      .some((item) => (item.bullets || item.highlights || []).some((x) => String(x || '').trim()));
    const checks = [
      Boolean(b.name), Boolean(b.email || b.phone), Boolean(b.summary),
      (data.education || []).length > 0,
      (data.experience || []).length + (data.projects || []).length > 0,
      hasBullet, (data.skills || []).length > 0,
    ];
    return Math.round((checks.filter(Boolean).length / checks.length) * 100);
  }, [data]);

  if (loading) return <p role="status" style={{ padding: 24 }}>简历加载中…</p>;

  return (
    <div className="resume-studio">
      <header className="resume-head">
        <div className="resume-head-main">
          <h1><FileText size={20} aria-hidden="true" /> 简历工作台</h1>
          <p>上传已有简历自动填充，或直接结构化填写；右侧实时预览，改完一键导出 PDF。</p>
        </div>
        <Space wrap>
          <Select
            value={current?.id}
            style={{ minWidth: 170 }}
            onChange={switchResume}
            options={list.map((item) => ({ value: item.id, label: item.title }))}
          />
          <Button icon={<Plus size={15} />} onClick={createResume}>新建</Button>
          <Button icon={<History size={15} />} onClick={openVersions}>版本</Button>
          <Button type="primary" icon={<Save size={15} />} loading={saving} onClick={() => persist({ snapshot: true, note: '手动保存' })}>
            保存并留档
          </Button>
          <Button icon={<Download size={15} />} loading={exporting} onClick={exportPdf}>导出 PDF</Button>
        </Space>
      </header>

      <Row gutter={16} className="resume-body">
        <Col xs={24} lg={14}>
          <div className="resume-editor">
            <Card size="small" className="resume-meta-card">
              <Row gutter={10}>
                <Col span={12}>
                  <label className="resume-label" htmlFor="resume-title">简历名称</label>
                  <Input id="resume-title" value={title} maxLength={40} onChange={(e) => { setTitle(e.target.value); markDirty(); }} />
                </Col>
                <Col span={12}>
                  <label className="resume-label" htmlFor="resume-role">目标岗位</label>
                  <Input id="resume-role" value={targetRole} maxLength={40} placeholder="如：产品实习生" onChange={(e) => { setTargetRole(e.target.value); markDirty(); }} />
                </Col>
              </Row>
            </Card>

            <ResumeAttachments
              resumeId={current?.id}
              onImported={(resume) => {
                // 导入是服务端直接改的正文，这里同步回编辑器并清掉未保存标记
                applyResume(resume);
                setList((prev) => prev.map((item) => (item.id === resume.id
                  ? { ...item, title: resume.title, target_role: resume.target_role, version: resume.version, updated_at: resume.updated_at }
                  : item)));
              }}
            />

            <SectionCard icon={<FileText size={15} />} title="基本信息">
              <Row gutter={10}>
                <Col span={12}><label className="resume-label">姓名</label>
                  <Input value={data.basics.name} maxLength={20} onChange={(e) => { setData((p) => ({ ...p, basics: { ...p.basics, name: e.target.value } })); markDirty(); }} /></Col>
                <Col span={12}><label className="resume-label">手机</label>
                  <Input value={data.basics.phone} maxLength={20} onChange={(e) => { setData((p) => ({ ...p, basics: { ...p.basics, phone: e.target.value } })); markDirty(); }} /></Col>
                <Col span={12}><label className="resume-label">邮箱</label>
                  <Input value={data.basics.email} maxLength={60} onChange={(e) => { setData((p) => ({ ...p, basics: { ...p.basics, email: e.target.value } })); markDirty(); }} /></Col>
                <Col span={12}><label className="resume-label">城市</label>
                  <Input value={data.basics.city} maxLength={20} onChange={(e) => { setData((p) => ({ ...p, basics: { ...p.basics, city: e.target.value } })); markDirty(); }} /></Col>
                <Col span={24}><label className="resume-label">作品集 / GitHub</label>
                  <Input value={data.basics.github} maxLength={100} placeholder="选填" onChange={(e) => { setData((p) => ({ ...p, basics: { ...p.basics, github: e.target.value } })); markDirty(); }} /></Col>
                <Col span={24}><label className="resume-label">一句话自我介绍</label>
                  <TextArea value={data.basics.summary} maxLength={300} autoSize={{ minRows: 2, maxRows: 5 }} placeholder="用 2 句话说清你是谁、擅长什么、想找什么方向" onChange={(e) => { setData((p) => ({ ...p, basics: { ...p.basics, summary: e.target.value } })); markDirty(); }} /></Col>
              </Row>
            </SectionCard>

            <SectionCard
              icon={<FileText size={15} />}
              title="教育经历"
              extra={<Button size="small" type="link" onClick={() => edu.add(EDU_TEMPLATE)}>+ 添加</Button>}
            >
              {!data.education.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没填教育经历" />}
              {data.education.map((item, index) => (
                <div className="resume-entry" key={index}>
                  <div className="resume-entry-head">
                    <span>教育经历 {index + 1}</span>
                    <EntryToolbar index={index} total={data.education.length} onMove={edu.move} onRemove={edu.remove} />
                  </div>
                  <Row gutter={10}>
                    <Col span={12}><Input placeholder="学校" value={item.school} onChange={(e) => { edu.patch(index, { school: e.target.value }); markDirty(); }} /></Col>
                    <Col span={12}><Input placeholder="专业" value={item.major} onChange={(e) => { edu.patch(index, { major: e.target.value }); markDirty(); }} /></Col>
                    <Col span={8}><Input placeholder="学历" value={item.degree} onChange={(e) => { edu.patch(index, { degree: e.target.value }); markDirty(); }} /></Col>
                    <Col span={8}><Input placeholder="起 2022.09" value={item.start} onChange={(e) => { edu.patch(index, { start: e.target.value }); markDirty(); }} /></Col>
                    <Col span={8}><Input placeholder="止 2026.06" value={item.end} onChange={(e) => { edu.patch(index, { end: e.target.value }); markDirty(); }} /></Col>
                    <Col span={24}><Input placeholder="GPA（选填）" value={item.gpa} onChange={(e) => { edu.patch(index, { gpa: e.target.value }); markDirty(); }} /></Col>
                    <Col span={24}><BulletInput value={item.highlights} placeholder="一行一条，例如：主修数据结构、数据库、统计学" onChange={(v) => { edu.patch(index, { highlights: v }); markDirty(); }} /></Col>
                  </Row>
                </div>
              ))}
            </SectionCard>

            <SectionCard
              icon={<FileText size={15} />}
              title="实习 / 工作经历"
              extra={<Button size="small" type="link" onClick={() => exp.add(EXP_TEMPLATE)}>+ 添加</Button>}
            >
              {!data.experience.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有实习也没关系，可以只写项目经历" />}
              {data.experience.map((item, index) => (
                <div className="resume-entry" key={index}>
                  <div className="resume-entry-head">
                    <span>实习 {index + 1}</span>
                    <EntryToolbar index={index} total={data.experience.length} onMove={exp.move} onRemove={exp.remove} />
                  </div>
                  <Row gutter={10}>
                    <Col span={12}><Input placeholder="公司" value={item.company} onChange={(e) => { exp.patch(index, { company: e.target.value }); markDirty(); }} /></Col>
                    <Col span={12}><Input placeholder="岗位" value={item.role} onChange={(e) => { exp.patch(index, { role: e.target.value }); markDirty(); }} /></Col>
                    <Col span={12}><Input placeholder="起 2025.07" value={item.start} onChange={(e) => { exp.patch(index, { start: e.target.value }); markDirty(); }} /></Col>
                    <Col span={12}><Input placeholder="止 2025.09" value={item.end} onChange={(e) => { exp.patch(index, { end: e.target.value }); markDirty(); }} /></Col>
                    <Col span={24}>
                      <div className="resume-bullet-head">
                        <span>做了什么</span>
                        <Button
                          size="small" type="link" icon={<Sparkles size={13} />}
                          loading={polishing === `experience-${index}`}
                          onClick={() => polish('实习经历', item, index, exp)}
                        >
                          AI 润色
                        </Button>
                      </div>
                      <BulletInput value={item.bullets} onChange={(v) => { exp.patch(index, { bullets: v }); markDirty(); }} />
                    </Col>
                  </Row>
                </div>
              ))}
            </SectionCard>

            <SectionCard
              icon={<FileText size={15} />}
              title="项目经历"
              extra={<Button size="small" type="link" onClick={() => proj.add(PROJ_TEMPLATE)}>+ 添加</Button>}
            >
              {!data.projects.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="课程项目、竞赛、自驱作品都可以写" />}
              {data.projects.map((item, index) => (
                <div className="resume-entry" key={index}>
                  <div className="resume-entry-head">
                    <span>项目 {index + 1}</span>
                    <EntryToolbar index={index} total={data.projects.length} onMove={proj.move} onRemove={proj.remove} />
                  </div>
                  <Row gutter={10}>
                    <Col span={12}><Input placeholder="项目名称" value={item.name} onChange={(e) => { proj.patch(index, { name: e.target.value }); markDirty(); }} /></Col>
                    <Col span={12}><Input placeholder="你的角色" value={item.role} onChange={(e) => { proj.patch(index, { role: e.target.value }); markDirty(); }} /></Col>
                    <Col span={12}><Input placeholder="起 2025.03" value={item.start} onChange={(e) => { proj.patch(index, { start: e.target.value }); markDirty(); }} /></Col>
                    <Col span={12}><Input placeholder="止 2025.06" value={item.end} onChange={(e) => { proj.patch(index, { end: e.target.value }); markDirty(); }} /></Col>
                    <Col span={24}>
                      <div className="resume-bullet-head">
                        <span>做了什么</span>
                        <Button
                          size="small" type="link" icon={<Sparkles size={13} />}
                          loading={polishing === `projects-${index}`}
                          onClick={() => polish('项目经历', item, index, proj)}
                        >
                          AI 润色
                        </Button>
                      </div>
                      <BulletInput value={item.bullets} onChange={(v) => { proj.patch(index, { bullets: v }); markDirty(); }} />
                    </Col>
                  </Row>
                </div>
              ))}
            </SectionCard>

            <SectionCard icon={<FileText size={15} />} title="技能">
              <Select
                mode="tags"
                style={{ width: '100%' }}
                value={data.skills}
                placeholder="输入后回车，例如：SQL、Excel、Axure"
                onChange={(value) => { setData((p) => ({ ...p, skills: value })); markDirty(); }}
                tokenSeparators={[',', '，', '、']}
              />
            </SectionCard>

            <SectionCard icon={<FileText size={15} />} title="奖项与证书">
              <BulletInput
                value={data.awards}
                placeholder="一行一条，例如：全国大学生数学建模竞赛省二等奖"
                onChange={(v) => { setData((p) => ({ ...p, awards: v })); markDirty(); }}
              />
            </SectionCard>

            <div className="resume-danger">
              <Popconfirm title="删除这份简历？" description="删除后版本记录也会一并清空" okText="删除" cancelText="取消" onConfirm={removeResume}>
                <Button danger size="small" icon={<Trash2 size={14} />}>删除这份简历</Button>
              </Popconfirm>
            </div>
          </div>
        </Col>

        <Col xs={24} lg={10}>
          <div className="resume-preview-wrap">
            <div className="resume-preview-bar">
              <span>实时预览</span>
              <Space size={8}>
                {dirty ? <Tag color="orange">未保存</Tag> : <Tag color="green">已保存</Tag>}
                <Tooltip title="招聘方真正会看的项，空着就扣分">
                  <span className="resume-score">完整度 {completeness}%</span>
                </Tooltip>
              </Space>
            </div>
            <Progress percent={completeness} size="small" showInfo={false} strokeColor="#4a6cf7" />
            <div className="resume-paper">
              <h2>{data.basics.name || '你的名字'}</h2>
              <p className="resume-paper-contact">
                {[data.basics.phone, data.basics.email, data.basics.city, data.basics.github]
                  .filter(Boolean).join('  |  ') || '手机 · 邮箱 · 城市'}
              </p>
              {data.basics.summary && <p className="resume-paper-summary">{data.basics.summary}</p>}

              {shown.education.length > 0 && (
                <>
                  <h3>教育经历</h3>
                  {shown.education.map((item, i) => (
                    <div className="resume-paper-entry" key={i}>
                      <div className="resume-paper-row">
                        <strong>{[item.school, item.major, item.degree].filter(Boolean).join(' · ')}</strong>
                        <span>{[item.start, item.end].filter(Boolean).join(' – ')}</span>
                      </div>
                      {item.gpa && <p className="resume-paper-meta">GPA {item.gpa}</p>}
                      <ul>{(item.highlights || []).filter(Boolean).map((h, j) => <li key={j}>{h}</li>)}</ul>
                    </div>
                  ))}
                </>
              )}

              {shown.experience.length > 0 && (
                <>
                  <h3>实习 / 工作经历</h3>
                  {shown.experience.map((item, i) => (
                    <div className="resume-paper-entry" key={i}>
                      <div className="resume-paper-row">
                        <strong>{[item.company, item.role].filter(Boolean).join(' · ')}</strong>
                        <span>{[item.start, item.end].filter(Boolean).join(' – ')}</span>
                      </div>
                      <ul>{(item.bullets || []).filter(Boolean).map((b, j) => <li key={j}>{b}</li>)}</ul>
                    </div>
                  ))}
                </>
              )}

              {shown.projects.length > 0 && (
                <>
                  <h3>项目经历</h3>
                  {shown.projects.map((item, i) => (
                    <div className="resume-paper-entry" key={i}>
                      <div className="resume-paper-row">
                        <strong>{[item.name, item.role].filter(Boolean).join(' · ')}</strong>
                        <span>{[item.start, item.end].filter(Boolean).join(' – ')}</span>
                      </div>
                      <ul>{(item.bullets || []).filter(Boolean).map((b, j) => <li key={j}>{b}</li>)}</ul>
                    </div>
                  ))}
                </>
              )}

              {data.skills.length > 0 && (<><h3>技能</h3><p>{data.skills.join('、')}</p></>)}
              {data.awards.length > 0 && (<><h3>奖项与证书</h3><ul>{data.awards.filter(Boolean).map((a, i) => <li key={i}>{a}</li>)}</ul></>)}
            </div>
          </div>
        </Col>
      </Row>

      <Drawer title="版本历史" open={versionOpen} onClose={() => setVersionOpen(false)} width={380}>
        <Button block icon={<Save size={14} />} onClick={snapshotNow} style={{ marginBottom: 12 }}>
          把当前内容存为一个版本
        </Button>
        <List
          loading={versionLoading}
          dataSource={versions}
          locale={{ emptyText: '还没有版本记录' }}
          renderItem={(item) => (
            <List.Item
              actions={item.is_current ? [<Tag key="cur" color="blue">当前</Tag>] : [
                <Popconfirm key="r" title={`恢复到 v${item.version}？`} okText="恢复" cancelText="取消" onConfirm={() => restore(item.version)}>
                  <Button size="small" type="link">恢复</Button>
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={`v${item.version} · ${item.note || '未命名'}`}
                description={item.created_at ? new Date(item.created_at).toLocaleString('zh-CN') : ''}
              />
            </List.Item>
          )}
        />
      </Drawer>
    </div>
  );
}
