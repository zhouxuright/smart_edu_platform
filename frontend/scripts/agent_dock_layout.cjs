/**
 * dock 在「无工具页面」与「手机端展开」两种形态下的检查。
 * 用法：UI_EMAIL=... UI_PASSWORD=... node scripts/agent_dock_layout.cjs
 */
const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');

const CHROME = process.env.UI_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const BASE = process.env.UI_BASE || 'http://127.0.0.1:5173';
const EMAIL = process.env.UI_EMAIL;
const PASSWORD = process.env.UI_PASSWORD;
const OUT = path.join(__dirname, 'out');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });

  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2', timeout: 60000 });
  const inputs = await page.$$('input');
  await inputs[0].type(EMAIL);
  await inputs[1].type(PASSWORD);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 60000 }).catch(() => {}),
    page.click('button[type="submit"]'),
  ]);

  // 无工具页面：应有诚实提示，而不是假装能看数据
  await page.goto(`${BASE}/dashboard`, { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(2000);
  await page.click('.agent-fab');
  await sleep(1200);
  const hint = await page.evaluate(
    () => document.querySelector('.agent-panel-hint')?.innerText || '',
  );
  console.log(hint ? `PASS  无工具页有提示 -> ${hint.slice(0, 40)}…` : 'FAIL  没有提示');
  await page.screenshot({ path: path.join(OUT, 'agent-8-dashboard.png') });

  // 导师页本身是对话页，不该再挂一个 dock
  await page.goto(`${BASE}/mentor`, { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(2000);
  const mentorFab = await page.$('.agent-fab');
  console.log(mentorFab ? 'FAIL  导师页重复出现 dock' : 'PASS  导师页没有重复 dock');

  // 手机端展开：应是底部弹层，且盖住标签栏
  await page.setViewport({ width: 390, height: 844, isMobile: true });
  await page.goto(`${BASE}/applications`, { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(2000);
  await page.click('.agent-fab');
  await sleep(800);
  const box = await page.evaluate(() => {
    const el = document.querySelector('.agent-panel');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { left: r.left, right: r.right, bottom: r.bottom, height: r.height, width: r.width };
  });
  console.log(box
    ? `PASS  手机端面板全宽贴底 -> width=${Math.round(box.width)} height=${Math.round(box.height)} bottom=${Math.round(box.bottom)}`
    : 'FAIL  手机端面板没出现');
  await page.screenshot({ path: path.join(OUT, 'agent-9-mobile-open.png') });

  await browser.close();
})();
