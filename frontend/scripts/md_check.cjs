/**
 * Markdown 渲染验收：登录 → 进生涯导师 → 提一个问题 → 等流式结束 →
 * 检查助手气泡里是否还有裸露的 Markdown 标记，以及是否真的渲染出了结构。
 *
 * 用法（在 frontend 目录）：
 *   UI_EMAIL=xx@example.com UI_PASSWORD=xxx node scripts/md_check.cjs
 */
const puppeteer = require('puppeteer');

const CHROME = process.env.UI_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const BASE = process.env.UI_BASE || 'http://127.0.0.1:5173';
const EMAIL = process.env.UI_EMAIL;
const PASSWORD = process.env.UI_PASSWORD;
const QUESTION = process.env.UI_QUESTION || '我是大三学生，想找第一份产品实习，接下来三周怎么准备？';

if (!EMAIL || !PASSWORD) {
  console.error('请通过 UI_EMAIL / UI_PASSWORD 提供测试账号');
  process.exit(1);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 1000 });

  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));

  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle2', timeout: 60000 });
  const inputs = await page.$$('input');
  await inputs[0].type(EMAIL);
  await inputs[1].type(PASSWORD);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle2', timeout: 60000 }).catch(() => {}),
    page.click('button[type="submit"]'),
  ]);

  await page.goto(`${BASE}/mentor?focus=career`, { waitUntil: 'networkidle2', timeout: 60000 });
  await new Promise((r) => setTimeout(r, 1500));

  await page.type('#mentor-question', QUESTION);
  await page.click('.mentor-composer button[type="submit"]');

  // 等流式结束：loading 提示消失且助手气泡出现
  await page.waitForFunction(
    () => !document.querySelector('.mentor-loading') && document.querySelectorAll('.mentor-message-assistant').length > 0,
    { timeout: 120000, polling: 400 },
  ).catch(() => console.log('⚠️ 等待超时，按当前 DOM 结果检查'));
  await new Promise((r) => setTimeout(r, 800));

  const report = await page.evaluate(() => {
    const bubbles = [...document.querySelectorAll('.mentor-message-assistant')];
    const last = bubbles[bubbles.length - 1];
    if (!last) return { found: false };
    const text = last.innerText || '';
    return {
      found: true,
      chars: text.length,
      strong: last.querySelectorAll('strong').length,
      h: last.querySelectorAll('h1,h2,h3,h4,h5,h6').length,
      ul: last.querySelectorAll('ul,ol').length,
      li: last.querySelectorAll('li').length,
      hr: last.querySelectorAll('hr').length,
      table: last.querySelectorAll('table').length,
      code: last.querySelectorAll('code').length,
      rawBold: (text.match(/\*\*/g) || []).length,
      rawHash: (text.match(/(^|\n)#{1,6}\s/g) || []).length,
      rawDash: (text.match(/(?<!-)--(?!-)/g) || []).length,
      rawHr: (text.match(/(^|\n)---(\n|$)/g) || []).length,
      rawBullet: (text.match(/(^|\n)\*\s/g) || []).length,
      preview: text.slice(0, 300),
    };
  });

  console.log('渲染结果:', JSON.stringify(report, null, 2));
  const raw = ['rawBold', 'rawHash', 'rawDash', 'rawHr', 'rawBullet']
    .filter((k) => report[k] > 0)
    .map((k) => `${k}=${report[k]}`);
  console.log(raw.length ? `❌ 仍有裸露标记: ${raw.join(', ')}` : '✅ 无裸露 Markdown 标记');
  console.log(errors.length ? `控制台错误:\n${errors.join('\n')}` : '✅ 无控制台错误');

  await page.screenshot({ path: 'scripts/out/mentor-markdown.png', fullPage: false });
  await browser.close();
})();
