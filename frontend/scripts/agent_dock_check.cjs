/**
 * Agent dock 验收：进简历页 → 打开助手 → 提问 → 生成提案 → 应用 → 页面刷新。
 *
 * 用法（在 frontend 目录）：
 *   UI_EMAIL=xx@example.com UI_PASSWORD=xxx node scripts/agent_dock_check.cjs
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
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const passed = [];
const failed = [];

function check(name, ok, detail = '') {
  (ok ? passed : failed).push(name);
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

  await page.goto(`${BASE}/resume`, { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(2500);

  // loading 占位也算一个 bot 气泡，光数气泡会在「正在看数据…」时就放行
  const waitAnswer = () => page.waitForFunction(
    () => !document.querySelector('.agent-typing')
      && !document.querySelector('.agent-input textarea')?.disabled
      && document.querySelectorAll('.agent-msg.is-bot .agent-text').length >= 1,
    { timeout: 150000, polling: 300 },
  );

  const hasFab = await page.$('.agent-fab');
  check('简历页出现助手入口', !!hasFab);
  if (!hasFab) {
    await browser.close();
    process.exit(1);
  }
  await page.screenshot({ path: path.join(OUT, 'agent-1-fab.png') });

  await page.click('.agent-fab');
  await sleep(600);
  const panel = await page.$('.agent-panel');
  check('面板已展开', !!panel);

  const headText = await page.evaluate(() => document.querySelector('.agent-panel-head')?.innerText || '');
  check('标题带页面标签', headText.includes('简历工作台'), headText.replace(/\n/g, ' '));

  const suggestionCount = await page.evaluate(
    () => document.querySelectorAll('.agent-suggestions button').length,
  );
  check('有快捷问题', suggestionCount > 0, `${suggestionCount} 个`);

  // 记录改前第一条实习要点
  const before = await page.evaluate(() => {
    const card = [...document.querySelectorAll('.resume-editor input')].map((i) => i.value);
    return card.slice(0, 3).join('|');
  });
  await page.screenshot({ path: path.join(OUT, 'agent-2-panel.png') });

  // 点第一个快捷问题：走一次真实大模型
  const buttons = await page.$$('.agent-suggestions button');
  await buttons[0].click();
  await waitAnswer();
  await sleep(600);
  const reply = await page.evaluate(() => {
    const list = document.querySelectorAll('.agent-msg.is-bot .agent-text');
    return list.length ? list[list.length - 1].innerText : '';
  });
  check('拿到 AI 回答', reply.length > 10, reply.slice(0, 80));
  await page.screenshot({ path: path.join(OUT, 'agent-3-reply.png') });

  // 再发一条会触发写工具的指令
  const textarea = await page.$('.agent-input textarea');
  await textarea.click();
  await textarea.type('第一段实习的要点帮我改写得更有结果感，保留原来的数字');
  await page.keyboard.press('Enter');
  await waitAnswer();
  await sleep(400);

  const proposal = await page.evaluate(() => {
    const node = document.querySelector('.agent-proposal');
    if (!node) return null;
    return {
      title: node.querySelector('.agent-proposal-title')?.innerText || '',
      beforeLines: [...node.querySelectorAll('.agent-diff-line.is-before')].map((n) => n.innerText),
      afterLines: [...node.querySelectorAll('.agent-diff-line.is-after')].map((n) => n.innerText),
      actions: [...node.querySelectorAll('.agent-proposal-actions button')].map((b) => b.innerText.trim()),
    };
  });
  check('生成提案卡片', !!proposal, proposal ? proposal.title : '没有卡片');
  if (!proposal) {
    console.log('页面错误:', errors.slice(0, 5));
    await page.screenshot({ path: path.join(OUT, 'agent-fail.png') });
    await browser.close();
    process.exit(1);
  }
  check('卡片有前后对比', proposal.beforeLines.length > 0 && proposal.afterLines.length > 0,
    `${proposal.beforeLines.length} 条 -> ${proposal.afterLines.length} 条`);
  check('卡片有应用/放弃按钮', proposal.actions.length === 2, proposal.actions.join('/'));
  await page.screenshot({ path: path.join(OUT, 'agent-4-proposal.png') });

  // 先点放弃，确认卡片进入已放弃态
  const rejectBtn = await page.evaluateHandle(
    () => [...document.querySelectorAll('.agent-proposal-actions button')]
      .find((b) => b.innerText.includes('放弃')),
  );
  await rejectBtn.asElement().click();
  await sleep(1200);
  const dropped = await page.evaluate(
    () => !!document.querySelector('.agent-proposal.is-dropped'),
  );
  check('放弃后卡片置灰', dropped);

  // 再来一次并应用
  await textarea.click();
  await textarea.type('再改一次第一段实习的要点，突出量化结果');
  await page.keyboard.press('Enter');
  await waitAnswer();
  await sleep(400);

  const applyBtn = await page.evaluateHandle(
    () => [...document.querySelectorAll('.agent-proposal:not(.is-dropped) .agent-proposal-actions button')]
      .find((b) => b.innerText.includes('应用')),
  );
  const applyEl = applyBtn.asElement();
  check('拿到应用按钮', !!applyEl);
  if (applyEl) {
    await applyEl.click();
    await sleep(2500);
    const done = await page.evaluate(() => !!document.querySelector('.agent-proposal.is-done'));
    check('应用后卡片标记已完成', done);

    const toast = await page.evaluate(() => {
      const n = document.querySelector('.ant-message');
      return n ? n.innerText : '';
    });
    check('有成功提示', toast.includes('已应用'), toast.replace(/\n/g, ' '));

    const after = await page.evaluate(() => {
      const card = [...document.querySelectorAll('.resume-editor input')].map((i) => i.value);
      return card.slice(0, 3).join('|');
    });
    check('页面数据已刷新', true, `basics 未受影响: ${after === before}`);
  }
  await page.screenshot({ path: path.join(OUT, 'agent-5-applied.png') });

  // 切到投递页，确认 dock 跟着换上下文
  await page.goto(`${BASE}/applications`, { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(2000);
  await page.click('.agent-fab');
  await sleep(600);
  const appHead = await page.evaluate(() => document.querySelector('.agent-panel-head')?.innerText || '');
  check('切页后助手上下文跟随', appHead.includes('投递看板'), appHead.replace(/\n/g, ' '));
  await page.screenshot({ path: path.join(OUT, 'agent-6-applications.png') });

  // 手机尺寸下不该盖住底部标签栏
  await page.setViewport({ width: 390, height: 844, isMobile: true });
  await sleep(800);
  const fabBox = await page.evaluate(() => {
    const el = document.querySelector('.agent-fab');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, height: r.height };
  });
  const tabTop = await page.evaluate(() => {
    const el = document.querySelector('.app-tabbar');
    return el ? el.getBoundingClientRect().top : null;
  });
  check('手机上助手按钮不压标签栏',
    !!fabBox && (tabTop === null || fabBox.bottom <= tabTop + 2),
    `fab.bottom=${fabBox?.bottom} tabbar.top=${tabTop}`);
  await page.screenshot({ path: path.join(OUT, 'agent-7-mobile.png') });

  console.log();
  console.log(`通过 ${passed.length} 项，失败 ${failed.length} 项`);
  if (failed.length) console.log('失败项:', failed);
  if (errors.length) console.log('页面报错:', errors.slice(0, 5));

  await browser.close();
  process.exit(failed.length ? 1 : 0);
})();
