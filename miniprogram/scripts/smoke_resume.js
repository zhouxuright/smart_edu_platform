/**
 * 小程序页面逻辑冒烟测试。
 *
 * 本机没装微信开发者工具，无法真机/模拟器预览，所以这里用 Node 桩掉
 * `wx` 与 `Page`，把 pages/resume/resume.js 当普通模块加载，再按生命周期
 * 调用它的方法，请求打到真实 FastAPI。
 *
 * 覆盖：加载列表 → 批量上传（含临时文件名纠正）→ 文本预览 → AI 解析导入 → 删除。
 *
 * 用法：
 *   MP_TOKEN=<token> node scripts/smoke_resume.js
 */
const fs = require('fs');
const path = require('path');
const os = require('os');

const TOKEN = process.env.MP_TOKEN || '';
const BASE = process.env.MP_BASE || 'http://127.0.0.1:8000/api';

if (!TOKEN) {
  console.error('缺少 MP_TOKEN');
  process.exit(1);
}

const logs = [];
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mp-resume-'));
const pdfLike = path.join(tmpDir, 'tmp_a1b2c3.docx');
const txtLike = path.join(tmpDir, 'tmp_d4e5f6.txt');
const ORIGINAL_DOCX = '周洋-产品实习.docx';
const ORIGINAL_TXT = '求职备忘.txt';

global.wx = {
  getStorageSync: (key) => {
    if (key === 'qicheng_token') return TOKEN;
    // 让页面里的 BASE_URL 指向本次测试的后端，避免依赖局域网 IP
    if (key === 'qicheng_base_url') return BASE;
    return '';
  },
  setStorageSync: () => {},
  removeStorageSync: () => {},
  reLaunch: (opt) => logs.push(['reLaunch', opt.url]),

  request({ url, method = 'GET', data, header, success, fail }) {
    fetch(url, {
      method,
      headers: Object.assign({ 'Content-Type': 'application/json' }, header || {}),
      body: data ? JSON.stringify(data) : undefined,
    })
      .then(async (res) => success({ statusCode: res.status, data: await res.json().catch(() => ({})) }))
      .catch((err) => fail({ errMsg: `request:fail ${err.message}` }));
  },

  // 关键点：这里故意用临时文件名上传，验证 formData.filenames 能把原名纠正回来
  uploadFile({ url, filePath, name, formData, header, success, fail }) {
    const buf = fs.readFileSync(filePath);
    const fd = new FormData();
    Object.entries(formData || {}).forEach(([k, v]) => fd.append(k, v));
    fd.append(name, new Blob([buf]), path.basename(filePath));
    fetch(url, { method: 'POST', headers: header || {}, body: fd })
      .then(async (res) => success({ statusCode: res.status, data: await res.text() }))
      .catch((err) => fail({ errMsg: `uploadFile:fail ${err.message}` }));
  },

  downloadFile({ url, success }) {
    logs.push(['download', url.replace(BASE, '')]);
    success({ statusCode: 200, tempFilePath: path.join(tmpDir, 'dl.pdf') });
  },
  openDocument: (opt) => logs.push(['openDocument', opt.fileType]),
  showToast: (opt) => logs.push(['toast', opt.title]),
  showLoading: () => {},
  hideLoading: () => {},
  showModal(opt) {
    logs.push(['modal', opt.title, String(opt.content || '').replace(/\n/g, ' / ')]);
    // 自动点「确认」，把需要用户确认的分支也跑一遍
    if (opt.success) opt.success({ confirm: true, cancel: false });
  },
  chooseMessageFile: (opt) => opt.success({ tempFiles: [] }),
};

global.Page = (config) => { global.__page = config; };
require('../pages/resume/resume.js');

const config = global.__page;
const page = Object.create(config);
page.data = JSON.parse(JSON.stringify(config.data));

function setDeep(target, dotted, value) {
  const keys = dotted.split('.');
  let node = target;
  for (let i = 0; i < keys.length - 1; i += 1) {
    if (typeof node[keys[i]] !== 'object' || node[keys[i]] === null) node[keys[i]] = {};
    node = node[keys[i]];
  }
  node[keys[keys.length - 1]] = value;
}
page.setData = (patch) => {
  Object.entries(patch).forEach(([key, value]) => setDeep(page.data, key, value));
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function check(label, ok, detail) {
  console.log(`${ok ? '✅' : '❌'} ${label}${detail ? ` — ${detail}` : ''}`);
  if (!ok) process.exitCode = 1;
}

(async () => {
  // 用后端已有的 python-docx 造一份真实 docx，路径故意取临时名
  const { execFileSync } = require('child_process');
  const py = [
    'from docx import Document',
    `doc = Document()`,
    'for line in ["周洋", "电话 13300004444  邮箱 zhouyang@example.com  南京", "教育经历",',
    '             "2021.09-2025.06 某某大学 信息管理与信息系统 本科 GPA 3.6/4.0",',
    '             "实习经历",',
    '             "2024.07-2024.10 某 SaaS 公司 产品实习生，负责工单模块需求梳理，输出 2 份 PRD，上线后工单平均处理时长下降 18%",',
    '             "技能：Axure、SQL、Excel"]:',
    '    doc.add_paragraph(line)',
    `doc.save(r"${pdfLike.replace(/\\/g, '\\\\')}")`,
  ].join('\n');
  execFileSync('python', ['-c', py], { cwd: path.join(__dirname, '..', '..', 'backend') });
  fs.writeFileSync(txtLike, '求职备忘：优先 SaaS 产品实习，可实习每周 4 天，期望南京或杭州。', 'utf8');

  const files = [
    { path: pdfLike, name: ORIGINAL_DOCX, size: fs.statSync(pdfLike).size },
    { path: txtLike, name: ORIGINAL_TXT, size: fs.statSync(txtLike).size },
  ];

  await page.loadList();
  check('loadList 拿到简历', Boolean(page.data.currentId), `id=${page.data.currentId}`);
  const beforeIds = new Set(page.data.attachments.map((a) => a.id));

  await page.uploadFiles(files);
  const names = page.data.attachments.map((a) => a.filename);
  const added = page.data.attachments.filter((a) => !beforeIds.has(a.id));
  check('批量上传 2 个文件', added.length === 2, names.join(' / '));
  check('临时文件名被纠正为原始名', names.includes(ORIGINAL_DOCX) && names.includes(ORIGINAL_TXT));
  check('附件带上了大小/字数摘要', page.data.attachments.every((a) => a.metaText));

  const docx = page.data.attachments.find((a) => a.filename === ORIGINAL_DOCX);
  await page.onPreview({ currentTarget: { dataset: { id: docx.id } } });
  check('文本预览', page.data.preview.open && page.data.preview.text.length > 30,
    `len=${page.data.preview.text.length}`);
  page.closePreview();
  check('关闭预览', page.data.preview.open === false);

  page.onDownload({ currentTarget: { dataset: { id: docx.id } } });
  await sleep(400);
  check('下载并交给系统预览', logs.some((l) => l[0] === 'download') && logs.some((l) => l[0] === 'openDocument'));

  await page.onImport({ currentTarget: { dataset: { id: docx.id } } });
  await sleep(6000);
  const modal = logs.filter((l) => l[0] === 'modal').pop();
  check('解析确认弹窗', Boolean(modal && /导入为结构化简历/.test(modal[1])), modal && modal[2]);
  check('导入后回填姓名', page.data.basics.name === '周洋', page.data.basics.name);
  check('导入后完整度提升', page.data.completeness > 0, `${page.data.completeness}%`);

  const txt = page.data.attachments.find((a) => a.filename === ORIGINAL_TXT);
  page.onDelete({ currentTarget: { dataset: { id: txt.id } } });
  await sleep(1500);
  check('删除附件', !page.data.attachments.some((a) => a.id === txt.id));

  console.log('\n--- 小程序侧提示 ---');
  logs.forEach((l) => console.log(`  ${l.join(' | ')}`));
})().catch((err) => {
  console.error('FAILED:', err);
  process.exit(1);
});
