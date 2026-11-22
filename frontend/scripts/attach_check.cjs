/* 附件简历上传验收：批量上传 → 列表 → 文本预览 → AI 解析导入 → 移动端 */
const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');

const OUT = path.join(__dirname, 'out');
const ATTACH = path.join(OUT, 'attach');
const BASE = process.env.UI_BASE || 'http://127.0.0.1:5173';
const EMAIL = process.env.UI_EMAIL || 'mdtest_scerei@example.com';
const PASSWORD = process.env.UI_PASSWORD || 'Test1234!';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(page, fn, { timeout = 30000, label = 'condition' } = {}) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await page.evaluate(fn)) return true;
    await sleep(300);
  }
  throw new Error(`timeout waiting for ${label}`);
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2', timeout: 60000 });
  const inputs = await page.$$('input');
  await inputs[0].type(EMAIL);
  await inputs[1].type(PASSWORD);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 60000 }).catch(() => {}),
    page.click('button[type="submit"]'),
  ]);
  await sleep(1500);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: 'new',
    args: ['--no-sandbox'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1200 });
  const errors = [];
  page.on('pageerror', (e) => errors.push(`PAGEERROR ${String(e).slice(0, 200)}`));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`CONSOLE ${m.text().slice(0, 200)}`); });

  await login(page);
  await page.goto(`${BASE}/resume`, { waitUntil: 'networkidle2', timeout: 60000 });
  await waitFor(page, () => !!document.querySelector('.resume-attach'), { label: '附件区块' });

  const before = await page.evaluate(() => document.querySelectorAll('.resume-attach-list .ant-list-item').length);
  console.log('上传前列表条数:', before);

  // 1) 一次选 3 个文件（批量上传）
  const input = await page.$('.resume-attach input[type="file"]');
  if (!input) throw new Error('找不到上传输入框');
  await input.uploadFile(
    path.join(ATTACH, '陈晓-数据分析.pdf'),
    path.join(ATTACH, '赵敏-前端.docx'),
    path.join(ATTACH, '求职方向.txt'),
  );

  await waitFor(page, () => {
    const t = document.querySelector('.resume-attach-list');
    return t && t.querySelectorAll('.ant-list-item').length >= 3;
  }, { timeout: 60000, label: '附件入库' });
  await sleep(1200);

  const afterUpload = await page.evaluate(() => ({
    count: document.querySelectorAll('.resume-attach-list .ant-list-item').length,
    names: [...document.querySelectorAll('.resume-attach-name')].map((n) => n.innerText.trim()),
    metas: [...document.querySelectorAll('.resume-attach-meta')].map((n) => n.innerText.replace(/\s+/g, ' ').trim()),
    hint: document.querySelector('.resume-attach-hint')?.innerText.trim(),
    alert: document.querySelector('.resume-attach-alert')?.innerText.replace(/\s+/g, ' ').trim() || null,
    overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
  }));
  console.log('上传后:', JSON.stringify(afterUpload, null, 2));
  await page.screenshot({ path: path.join(OUT, 'resume-attach-desktop.png') });

  // 2) 重复上传同一批 → 应提示重复而不是新增
  await input.uploadFile(path.join(ATTACH, '陈晓-数据分析.pdf'));
  await sleep(3500);
  const dup = await page.evaluate(() => ({
    count: document.querySelectorAll('.resume-attach-list .ant-list-item').length,
    alert: document.querySelector('.resume-attach-alert')?.innerText.replace(/\s+/g, ' ').trim() || null,
  }));
  console.log('重复上传:', JSON.stringify(dup));

  // 3) 文本预览
  const firstActionButtons = await page.$$('.resume-attach-list .ant-list-item:first-child .ant-list-item-action button');
  await firstActionButtons[0].click();
  await waitFor(page, () => {
    const pre = document.querySelector('.resume-attach-preview');
    return pre && pre.innerText.trim().length > 30;
  }, { timeout: 20000, label: '文本预览' });
  const previewLen = await page.evaluate(() => document.querySelector('.resume-attach-preview').innerText.trim().length);
  console.log('预览文字长度:', previewLen);
  await page.screenshot({ path: path.join(OUT, 'resume-attach-preview.png') });
  await page.keyboard.press('Escape');
  await sleep(600);

  // 4) AI 解析导入（PDF 那条）
  const rows = await page.$$('.resume-attach-list .ant-list-item');
  let pdfRow = null;
  for (const row of rows) {
    const name = await row.evaluate((el) => el.querySelector('.resume-attach-name')?.innerText || '');
    if (name.includes('.pdf')) { pdfRow = row; break; }
  }
  if (!pdfRow) throw new Error('找不到 PDF 附件行');
  const btns = await pdfRow.$$('.ant-list-item-action button');
  await btns[2].click();
  await waitFor(page, () => {
    const m = [...document.querySelectorAll('.ant-modal-content')];
    return m.some((x) => x.innerText.includes('覆盖当前简历'));
  }, { timeout: 90000, label: '解析结果弹窗' });
  const modalText = await page.evaluate(() => {
    const m = [...document.querySelectorAll('.ant-modal-content')].find((x) => x.innerText.includes('覆盖当前简历'));
    return m?.innerText.replace(/\s+/g, ' ').trim() || '';
  });
  console.log('解析弹窗:', modalText.slice(0, 260));
  await page.screenshot({ path: path.join(OUT, 'resume-attach-import.png') });

  const okBtn = await page.evaluateHandle(() => {
    const m = [...document.querySelectorAll('.ant-modal-content')].find((x) => x.innerText.includes('覆盖当前简历'));
    return [...m.querySelectorAll('button')].find((b) => b.innerText.includes('确认导入'));
  });
  await okBtn.asElement().click();
  await waitFor(page, () => {
    const name = document.querySelector('#resume-title');
    const inputs = [...document.querySelectorAll('.resume-section input')];
    return inputs.some((i) => i.value && i.value.length >= 2);
  }, { timeout: 60000, label: '导入后回填' });
  await sleep(1200);

  const imported = await page.evaluate(() => {
    const basicsCard = [...document.querySelectorAll('.resume-section')].find((c) => c.innerText.includes('基本信息'));
    const inputs = [...(basicsCard?.querySelectorAll('input') || [])].map((i) => i.value);
    return {
      basics: inputs.slice(0, 5),
      paperName: document.querySelector('.resume-paper h2')?.innerText,
      score: document.querySelector('.resume-score')?.innerText,
      dirty: document.querySelector('.resume-preview-bar')?.innerText.includes('未保存'),
    };
  });
  console.log('导入结果:', JSON.stringify(imported));
  await page.screenshot({ path: path.join(OUT, 'resume-attach-imported.png') });

  // 5) 移动端
  await page.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await page.reload({ waitUntil: 'networkidle2', timeout: 60000 });
  await waitFor(page, () => !!document.querySelector('.resume-attach'), { label: '移动端附件区块' });
  await sleep(1500);
  await page.evaluate(() => document.querySelector('.resume-attach')?.scrollIntoView({ block: 'start' }));
  await sleep(600);
  const mobile = await page.evaluate(() => ({
    overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
    dropWidth: Math.round(document.querySelector('.resume-attach-drop')?.getBoundingClientRect().width || 0),
    rows: document.querySelectorAll('.resume-attach-list .ant-list-item').length,
  }));
  console.log('移动端:', JSON.stringify(mobile));
  await page.screenshot({ path: path.join(OUT, 'resume-attach-mobile.png') });

  console.log('控制台错误:', errors.length ? errors : '无');
  fs.writeFileSync(path.join(OUT, 'attach-check.json'), JSON.stringify({ afterUpload, dup, previewLen, imported, mobile, errors }, null, 2));
  await browser.close();
})().catch((e) => { console.error('FAILED:', e.message); process.exit(1); });
