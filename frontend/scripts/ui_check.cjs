/**
 * 本地 UI 验收：登录后逐页截图，并把控制台错误打出来。
 *
 * 用法（在 frontend 目录）：
 *   node scripts/ui_check.cjs
 *   node scripts/ui_check.cjs /dashboard /rag
 *
 * 账号可用环境变量覆盖：UI_EMAIL / UI_PASSWORD
 */
const puppeteer = require('puppeteer');
const path = require('path');

const CHROME = process.env.UI_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const BASE = process.env.UI_BASE || 'http://127.0.0.1:5173';
const EMAIL = process.env.UI_EMAIL || 'e2e_eval_1795264961@example.com';
const PASSWORD = process.env.UI_PASSWORD || 'EvalPass123';

const DEFAULT_PATHS = ['/dashboard', '/plan/internship', '/rag', '/mistake-book', '/knowledge-extraction'];

// Git Bash 会把 /dashboard 这类参数当成本地路径转换掉，因此同时接受 dashboard 写法
const rawArgs = process.argv.slice(2).filter((a) => !a.startsWith('-'));
const paths = rawArgs.map((a) => (a.startsWith('/') ? a : `/${a}`));
const targets = paths.length ? paths : DEFAULT_PATHS;

const slug = (p) => p.replace(/^\//, '').replace(/\//g, '_') || 'root';

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 950 });

  const errors = [];
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`[console] ${m.text().slice(0, 300)}`);
  });
  page.on('pageerror', (e) => errors.push(`[pageerror] ${String(e).slice(0, 300)}`));

  console.log('登录…');
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2', timeout: 60000 });
  const inputs = await page.$$('input');
  await inputs[0].type(EMAIL);
  await inputs[1].type(PASSWORD);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 60000 }).catch(() => {}),
    page.click('button[type="submit"]'),
  ]);
  console.log('当前 URL:', page.url());

  for (const p of targets) {
    console.log(`\n=== ${p}`);
    await page.goto(`${BASE}${p}`, { waitUntil: 'networkidle2', timeout: 60000 });
    await new Promise((r) => setTimeout(r, 3200));

    const info = await page.evaluate(() => {
      const body = getComputedStyle(document.body);
      const text = document.body.innerText;
      // 统计「白字」风险：元素文字颜色接近白色
      let whiteText = 0;
      document.querySelectorAll('h1,h2,h3,h4,p,span,div').forEach((el) => {
        if (!el.textContent || el.children.length) return;
        const c = getComputedStyle(el).color;
        const m = c.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
        if (m && +m[1] > 235 && +m[2] > 235 && +m[3] > 235) whiteText += 1;
      });
      return {
        bodyBg: body.backgroundColor,
        whiteText,
        hasMenu: text.includes('我的计划'),
        menuItems: [...document.querySelectorAll('.ant-menu-item')].map((n) => n.innerText.trim()),
        textHead: text.slice(0, 220).replace(/\n+/g, ' | '),
      };
    });

    console.log('  菜单:', info.menuItems.join(' / ') || '(无侧栏)');
    console.log('  含「我的计划」:', info.hasMenu, '| 近白文字节点:', info.whiteText);
    console.log('  正文:', info.textHead);

    const file = path.join(__dirname, '..', `ui_${slug(p)}.png`);
    await page.screenshot({ path: file, fullPage: false });
    console.log('  截图:', path.basename(file));
  }

  if (errors.length) {
    console.log('\n控制台错误:');
    errors.slice(0, 12).forEach((e) => console.log('  ' + e));
  } else {
    console.log('\n无控制台错误');
  }

  await browser.close();
})().catch((e) => {
  console.error('FAILED:', e.message);
  process.exit(1);
});
