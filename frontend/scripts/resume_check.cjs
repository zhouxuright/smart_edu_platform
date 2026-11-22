/**
 * 简历工作台验收：进入页面 → 填基本信息 → 加一条实习 → 导出 PDF。
 *
 * 用法（在 frontend 目录）：
 *   UI_EMAIL=xx@example.com UI_PASSWORD=xxx node scripts/resume_check.cjs
 */
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const CHROME = process.env.UI_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const BASE = process.env.UI_BASE || 'http://127.0.0.1:5173';
const EMAIL = process.env.UI_EMAIL;
const PASSWORD = process.env.UI_PASSWORD;

if (!EMAIL || !PASSWORD) {
  console.error('请通过 UI_EMAIL / UI_PASSWORD 提供测试账号');
  process.exit(1);
}

const OUT = path.join(__dirname, 'out');
const DOWNLOAD = path.join(OUT, 'downloads');

(async () => {
  fs.mkdirSync(DOWNLOAD, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });

  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));

  const client = await page.createCDPSession();
  await client.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: DOWNLOAD });

  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2', timeout: 60000 });
  const inputs = await page.$$('input');
  await inputs[0].type(EMAIL);
  await inputs[1].type(PASSWORD);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 60000 }).catch(() => {}),
    page.click('button[type="submit"]'),
  ]);

  await page.goto(`${BASE}/resume`, { waitUntil: 'networkidle2', timeout: 60000 });
  await new Promise((r) => setTimeout(r, 2500));

  // 菜单是否出现「简历工作台」
  const hasMenu = await page.evaluate(() => document.body.innerText.includes('简历工作台'));

  // 填基本信息（第 1 个输入是简历名称，第 2 个是目标岗位，第 3 个开始是 basics）
  const fill = async (index, value) => {
    const els = await page.$$('.resume-editor input');
    if (els[index]) {
      await els[index].click({ clickCount: 3 });
      await els[index].type(value);
    }
  };
  await fill(2, '张一鸣');
  await fill(3, '13800000000');
  await fill(4, 'zhang@example.com');
  await fill(5, '杭州');

  // 加一条实习
  const added = await page.evaluate(() => {
    const btn = [...document.querySelectorAll('.ant-card-head button')].find((b) => b.innerText.includes('添加'));
    const cards = [...document.querySelectorAll('.resume-section')];
    const expCard = cards.find((c) => c.innerText.includes('实习 / 工作经历'));
    const add = expCard?.querySelector('.ant-card-head button');
    if (add) { add.click(); return true; }
    return Boolean(btn);
  });
  await new Promise((r) => setTimeout(r, 600));

  const previewText = await page.evaluate(() => document.querySelector('.resume-paper')?.innerText || '');
  const score = await page.evaluate(() => document.querySelector('.resume-score')?.innerText || '');

  await page.screenshot({ path: path.join(OUT, 'resume-studio.png'), fullPage: false });

  // 导出 PDF
  const before = new Set(fs.readdirSync(DOWNLOAD));
  await page.evaluate(() => {
    const btn = [...document.querySelectorAll('button')].find((b) => b.innerText.includes('导出 PDF'));
    if (btn) btn.click();
  });
  let pdfName = null;
  for (let i = 0; i < 30; i += 1) {
    await new Promise((r) => setTimeout(r, 500));
    const now = fs.readdirSync(DOWNLOAD).filter((f) => f.endsWith('.pdf'));
    const fresh = now.find((f) => !before.has(f));
    if (fresh) { pdfName = fresh; break; }
  }

  console.log(JSON.stringify({
    menuVisible: hasMenu,
    addEntryClicked: added,
    score,
    previewHasName: previewText.includes('张一鸣'),
    previewHasSection: previewText.includes('实习'),
    pdfDownloaded: pdfName,
    consoleErrors: errors,
  }, null, 2));

  await browser.close();
})();
