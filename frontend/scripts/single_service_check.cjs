/**
 * 单服务模式验收：一个容器同时提供前端页面和 API。
 *
 * 验证的是「部署形态」而不是业务逻辑：
 *   1. 首页能渲染（不是 JSON、不是空白）
 *   2. 直接访问深层链接不 404（history 路由回退到 index.html）
 *   3. 登录能拿到 token 并跳转（说明 /api 走的是同源，没被 SPA 兜底吞掉）
 *   4. 刷新后仍停留在原页面（token 持久化 + 路由正常）
 *
 * 用法（在 frontend 目录）：
 *   UI_BASE=http://127.0.0.1:8012 UI_EMAIL=xx UI_PASSWORD=xx node scripts/single_service_check.cjs
 */
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const CHROME = process.env.UI_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const BASE = process.env.UI_BASE || 'http://127.0.0.1:8012';
const EMAIL = process.env.UI_EMAIL;
const PASSWORD = process.env.UI_PASSWORD;

if (!EMAIL || !PASSWORD) {
  console.error('请通过 UI_EMAIL / UI_PASSWORD 提供测试账号');
  process.exit(1);
}

const OUT = path.join(__dirname, 'out');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let pass = 0;
let fail = 0;

function check(name, ok, detail = '') {
  ok ? pass++ : fail++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  -> ${detail}` : ''}`);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1000 });

  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 160)); });
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 160)));

  // ---- 1. 首页渲染
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle2', timeout: 60000 });
  const title = await page.title();
  const hasRoot = await page.$('#root');
  const rootHtml = await page.evaluate(() => document.querySelector('#root')?.innerHTML?.length || 0);
  check('首页返回 HTML 而不是 JSON', title.includes('启程'), title);
  check('React 已挂载', !!hasRoot && rootHtml > 500, `#root 内容 ${rootHtml} 字符`);
  await page.screenshot({ path: path.join(OUT, 'single-1-home.png') });

  // ---- 2. 深层链接直接访问
  const deep = await page.goto(`${BASE}/resume`, { waitUntil: 'networkidle2', timeout: 60000 });
  const deepStatus = deep.status();
  const deepTitle = await page.title();
  check('深层链接 /resume 返回 200', deepStatus === 200, `HTTP ${deepStatus}`);
  check('深层链接落到前端而不是 404 页', deepTitle.includes('启程'), deepTitle);

  // ---- 3. 登录（同源调用 /api）
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2', timeout: 60000 });
  const inputs = await page.$$('input');
  check('登录表单存在', inputs.length >= 2, `${inputs.length} 个输入框`);
  if (inputs.length < 2) {
    await browser.close();
    process.exit(1);
  }
  await inputs[0].type(EMAIL);
  await inputs[1].type(PASSWORD);
  const apiResponses = [];
  page.on('response', (r) => {
    if (r.url().includes('/api/')) apiResponses.push({ url: r.url(), status: r.status() });
  });
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 60000 }).catch(() => {}),
    page.click('button[type="submit"]'),
  ]);
  await sleep(2500);

  const token = await page.evaluate(() => localStorage.getItem('token') || localStorage.getItem('access_token'));
  check('登录后本地存了 token', !!token, token ? `长度 ${token.length}` : '未找到');
  const loginApi = apiResponses.find((r) => r.url.includes('/api/login'));
  check('登录走的是同源 /api/login', !!loginApi && loginApi.status === 200,
    loginApi ? `HTTP ${loginApi.status}` : '没抓到请求');
  check('登录后离开了登录页', !page.url().includes('/login'), page.url().replace(BASE, ''));
  await page.screenshot({ path: path.join(OUT, 'single-2-after-login.png') });

  // ---- 4. 刷新后保持
  const before = page.url();
  await page.reload({ waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(1500);
  check('刷新后仍停在原页面', page.url() === before, page.url().replace(BASE, ''));

  // ---- 5. 控制台错误
  const real = errors.filter((e) => !/favicon|Download the React DevTools/i.test(e));
  check('无控制台错误', real.length === 0, real.slice(0, 2).join(' | '));

  console.log(`\n通过 ${pass}，失败 ${fail}`);
  await browser.close();
  process.exit(fail ? 1 : 0);
})();
