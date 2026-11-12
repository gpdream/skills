#!/usr/bin/env node
'use strict';

const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const { chromium } = require(process.env.BIRDSYNC_PLAYWRIGHT_DIR);

const services = {
  birdreport: 'https://www.birdreport.cn/member/login.html',
  ebird: 'https://ebird.org/import/upload.form',
};
const allowedKeys = new Set(['Enter', 'Tab', 'Backspace', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight']);
const viewport = { width: 1280, height: 820 };

function argumentsFrom(argv) {
  const out = { service: 'birdreport', port: 43117, stateDir: path.join(os.homedir(), '.config', 'sync-bird-records', 'browser'), probe: false, fetchChecklist: null, output: null };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--service') out.service = argv[++i];
    else if (argv[i] === '--port') out.port = Number(argv[++i]);
    else if (argv[i] === '--state-dir') out.stateDir = path.resolve(argv[++i]);
    else if (argv[i] === '--probe') out.probe = true;
    else if (argv[i] === '--fetch-checklist') { out.fetchChecklist = argv[++i]; out.service = 'ebird'; }
    else if (argv[i] === '--output') out.output = path.resolve(argv[++i]);
    else throw new Error(`Unknown argument: ${argv[i]}`);
  }
  if (!services[out.service] || !Number.isInteger(out.port)) throw new Error('Invalid service or port');
  if (out.fetchChecklist && (!/^S\d+$/.test(out.fetchChecklist) || !out.output)) throw new Error('--fetch-checklist requires an S-number and --output');
  return out;
}

function newestChromium() {
  const cache = path.join(os.homedir(), '.cache', 'ms-playwright');
  if (!fs.existsSync(cache)) return null;
  const found = [];
  for (const folder of fs.readdirSync(cache)) {
    if (!folder.startsWith('chromium-')) continue;
    for (const relative of ['chrome-linux64/chrome', 'chrome-linux/chrome']) {
      const file = path.join(cache, folder, relative);
      if (fs.existsSync(file)) found.push(file);
    }
  }
  return found.sort().reverse()[0] || null;
}

function writeProtectedJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  fs.chmodSync(path.dirname(file), 0o700);
  const temporary = `${file}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(temporary, file);
  fs.chmodSync(file, 0o600);
}

function saveBirdReportCredential(rawUser) {
  let parsed;
  try { parsed = JSON.parse(rawUser); } catch { return false; }
  const token = [parsed?.token, parsed?.authToken, parsed?.accessToken, parsed?.data?.token]
    .find(value => typeof value === 'string' && value.trim());
  if (!token) return false;
  const file = path.join(os.homedir(), '.config', 'sync-bird-records', 'credentials.json');
  let credentials = {};
  try { credentials = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { credentials = {}; }
  credentials.birdreport_token = token.replace(/\s/g, '');
  writeProtectedJson(file, credentials);
  return true;
}

async function saveEbirdSession(context) {
  const cookies = await context.cookies(['https://ebird.org', 'https://secure.birds.cornell.edu']);
  const session = cookies.find(cookie => cookie.name === 'EBIRD_SESSIONID' && cookie.value);
  if (!session) return false;
  const file = path.join(os.homedir(), '.config', 'sync-bird-records', 'web-sessions.json');
  let sessions = {};
  try { sessions = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { sessions = {}; }
  sessions.ebird = {
    cookies: { EBIRD_SESSIONID: session.value },
    updated_at: new Date().toISOString(),
  };
  writeProtectedJson(file, sessions);
  return true;
}

async function loadEbirdSession(context) {
  const file = path.join(os.homedir(), '.config', 'sync-bird-records', 'web-sessions.json');
  let sessions = {};
  try { sessions = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return false; }
  const sessionId = sessions?.ebird?.cookies?.EBIRD_SESSIONID;
  if (!sessionId) return false;
  await context.addCookies([{
    name: 'EBIRD_SESSIONID', value: String(sessionId), domain: '.ebird.org', path: '/', secure: true, httpOnly: true, sameSite: 'Lax',
  }]);
  return true;
}

const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>观鸟同步 - 安全登录</title><style>
:root{color-scheme:light;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;background:#f3f6f2;color:#15231b}*{box-sizing:border-box}body{margin:0}
.top{position:sticky;top:0;z-index:2;background:#fff;border-bottom:1px solid #dce6de;padding:12px 16px;box-shadow:0 2px 12px #173b2420}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.brand{font-weight:750;margin-right:auto}.note{font-size:13px;color:#52645a}.status{font-size:13px;padding:5px 9px;border-radius:999px;background:#eef4ef}
button,input{font:inherit;border:1px solid #b9c8bc;border-radius:8px;padding:8px 11px;background:#fff}button{cursor:pointer}button.primary{background:#176b3a;color:#fff;border-color:#176b3a}.entry{min-width:250px;flex:1}.frame{max-width:1280px;margin:14px auto;background:#fff;box-shadow:0 4px 24px #173b2428;border-radius:10px;overflow:hidden;line-height:0}#screen{width:100%;height:auto;cursor:crosshair;background:#e8ece9}.help{max-width:1280px;margin:12px auto 24px;padding:0 10px;font-size:13px;color:#52645a;line-height:1.55}
</style></head><body><div class="top"><div class="row"><span class="brand">观鸟同步 · 安全登录</span><span id="status" class="status">正在连接浏览器…</span><button data-nav="birdreport">记录中心</button><button data-nav="ebird">eBird</button><button id="refresh">刷新</button></div>
<div class="row" style="margin-top:9px"><input id="entry" class="entry" type="password" autocomplete="off" placeholder="先点击下方网页输入框，再在这里输入账号/密码/验证码"><button id="send" class="primary">输入到网页</button><button data-key="Tab">Tab</button><button data-key="Enter">Enter</button><button data-key="Backspace">退格</button><button id="done">我已完成登录</button></div>
<div class="note" style="margin-top:8px">账号、密码和验证码只发送到下面的隔离浏览器，不写入日志；登录会话保存在本机受保护目录，Agent 后续接管筛选与同步。</div></div><div class="frame"><img id="screen" alt="远程登录网页截图"></div><div class="help">操作方法：点击截图中的输入框或按钮；需要输入文字时，在顶部安全输入框填写后点“输入到网页”。完成当前网站登录后，点“我已完成登录”。请只登录你本人的账户。</div>
<script>const q=s=>document.querySelector(s),screen=q('#screen'),status=q('#status'),entry=q('#entry');let busy=false;async function api(p,b){const r=await fetch(p,{method:b?'POST':'GET',headers:b?{'Content-Type':'application/json'}:{},body:b?JSON.stringify(b):undefined});if(!r.ok)throw new Error(await r.text());return r.headers.get('content-type')?.includes('json')?r.json():r.blob()}async function refresh(){if(busy)return;busy=true;try{const s=await api('status');status.textContent=(s.service==='birdreport'?'记录中心':'eBird')+' · '+s.title;screen.src='screenshot?t='+Date.now()}catch(e){status.textContent='连接失败'}finally{busy=false}}screen.addEventListener('click',async e=>{const r=screen.getBoundingClientRect();await api('click',{x:(e.clientX-r.left)*1280/r.width,y:(e.clientY-r.top)*820/r.height});setTimeout(refresh,350)});q('#send').onclick=async()=>{const text=entry.value;if(!text)return;await api('type',{text,clear:true});entry.value='';setTimeout(refresh,300)};entry.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();q('#send').click()}});document.querySelectorAll('[data-key]').forEach(b=>b.onclick=async()=>{await api('key',{key:b.dataset.key});setTimeout(refresh,250)});document.querySelectorAll('[data-nav]').forEach(b=>b.onclick=async()=>{await api('navigate',{service:b.dataset.nav});setTimeout(refresh,1200)});q('#refresh').onclick=refresh;q('#done').onclick=async()=>{const s=await api('complete',{});status.textContent=s.authenticated?'登录已识别，可以关闭此页':'尚未识别到登录，请确认网页已进入账户页面';setTimeout(refresh,800)};setInterval(refresh,1800);refresh();</script></body></html>`;

async function run() {
  const args = argumentsFrom(process.argv.slice(2));
  fs.mkdirSync(path.join(args.stateDir, 'profile'), { recursive: true, mode: 0o700 });
  fs.chmodSync(args.stateDir, 0o700);
  fs.chmodSync(path.join(args.stateDir, 'profile'), 0o700);
  const executablePath = newestChromium();
  if (!executablePath) throw new Error('Chromium is not installed in the Playwright cache');
  const context = await chromium.launchPersistentContext(path.join(args.stateDir, 'profile'), {
    headless: true,
    executablePath,
    viewport,
    locale: 'zh-CN',
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    args: ['--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'],
  });
  await context.addInitScript(() => { Object.defineProperty(navigator, 'webdriver', { get: () => undefined }); });
  const page = context.pages()[0] || await context.newPage();
  page.setDefaultTimeout(30000);
  let service = args.service;
  let operation = Promise.resolve();
  const enqueue = fn => { operation = operation.then(fn, fn); return operation; };
  const navigate = async name => {
    if (!services[name]) throw new Error('Unsupported service');
    service = name;
    await page.goto(services[name], { waitUntil: 'domcontentloaded', timeout: 60000 });
  };
  const authenticated = async () => {
    if (service === 'birdreport') {
      try {
        const rawUser = await page.evaluate(() => localStorage.getItem('$user') || '');
        const parsed = JSON.parse(rawUser);
        const token = [parsed?.token, parsed?.authToken, parsed?.accessToken, parsed?.data?.token]
          .find(value => typeof value === 'string' && value.trim());
        if (!token) return false;
        const response = await context.request.post('https://api.birdreport.cn/member/system/user/get', {
          headers: {
            Accept: 'application/json, text/javascript, */*; q=0.01',
            'Content-Type': 'application/json',
            Origin: 'https://www.birdreport.cn',
            Referer: 'https://www.birdreport.cn/',
            'X-Auth-Token': token.replace(/\s/g, ''),
          },
          data: {},
        });
        if (!response.ok()) return false;
        const envelope = await response.json();
        const code = Number(envelope?.code ?? 0);
        return Number.isFinite(code) && code >= 0 && envelope?.data && typeof envelope.data === 'object';
      } catch { return false; }
    }
    const url = new URL(page.url());
    if (!url.hostname.endsWith('ebird.org') || url.pathname.includes('/cassso/') || url.pathname.toLowerCase().includes('login')) return false;
    const cookies = await context.cookies(['https://ebird.org']);
    const hasSession = cookies.some(cookie => cookie.name === 'EBIRD_SESSIONID' && cookie.value);
    const hasUploadForm = await page.locator('input[type="file"], form[enctype*="multipart/form-data"]').count() > 0;
    return hasSession && url.pathname.includes('/import/') && hasUploadForm;
  };
  const captureAuthenticatedSession = async () => {
    if (service === 'ebird') return saveEbirdSession(context);
    let raw = '';
    try { raw = await page.evaluate(() => localStorage.getItem('$user') || ''); } catch { return false; }
    return saveBirdReportCredential(raw);
  };

  if (service === 'ebird') await loadEbirdSession(context);

  if (args.fetchChecklist) {
    const targetUrl = `https://ebird.org/checklist/${args.fetchChecklist}`;
    let navigationError = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        await page.goto(targetUrl, { waitUntil: 'commit', timeout: 60000 });
        navigationError = null;
        break;
      } catch (error) {
        navigationError = error;
        try {
          await page.waitForFunction(sourceId => {
            const observations = [...document.querySelectorAll('li[data-observation] section.Observation')];
            return document.querySelector('h1#content')?.textContent?.includes(sourceId)
              && observations.length > 0
              && observations.every(section => /^(?:\d+|X)$/i.test(section.querySelector('.Observation-numberObserved > span > span:last-child')?.textContent?.trim() || ''));
          }, args.fetchChecklist, { timeout: 5000 });
          navigationError = null;
          break;
        } catch {}
        await page.waitForTimeout(1000 * (attempt + 1));
      }
    }
    if (navigationError) throw navigationError;
    try {
      await page.waitForFunction(
        sourceId => {
          const observations = [...document.querySelectorAll('li[data-observation] section.Observation')];
          return document.querySelector('h1#content')?.textContent?.includes(sourceId)
            && observations.length > 0
            && observations.every(section => /^(?:\d+|X)$/i.test(section.querySelector('.Observation-numberObserved > span > span:last-child')?.textContent?.trim() || ''));
        },
        args.fetchChecklist,
        { timeout: 60000 },
      );
    } catch {
      console.log(JSON.stringify({ status: 'auth_required', service: 'ebird', checklist: args.fetchChecklist }));
      await context.close();
      process.exitCode = 2;
      return;
    }
    const body = await page.content();
    fs.mkdirSync(path.dirname(args.output), { recursive: true, mode: 0o700 });
    fs.writeFileSync(args.output, body, { mode: 0o600 });
    fs.chmodSync(args.output, 0o600);
    await saveEbirdSession(context);
    console.log(JSON.stringify({ status: 'ok', service: 'ebird', checklist: args.fetchChecklist, output: args.output }));
    await context.close();
    return;
  }
  await navigate(service);

  if (args.probe) {
    const ready = await authenticated();
    const captured = ready && await captureAuthenticatedSession();
    console.log(JSON.stringify({
      status: ready && captured ? 'valid' : 'auth_required',
      service,
      authenticated: ready,
      credentialCaptured: captured,
    }));
    await context.close();
    return;
  }

  const server = http.createServer((req, res) => {
    const route = new URL(req.url, 'http://localhost').pathname.replace(/\/$/, '') || '/';
    const json = (value, code = 200) => {
      const body = Buffer.from(JSON.stringify(value));
      res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': body.length, 'Cache-Control': 'no-store' }); res.end(body);
    };
    const fail = () => json({ error: 'browser_operation_failed' }, 400);
    if (req.method === 'GET') {
      if (route === '/') { const body = Buffer.from(html); res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Length': body.length, 'Cache-Control': 'no-store' }); return res.end(body); }
      if (route === '/screenshot') return enqueue(() => page.screenshot({ type: 'png' })).then(body => { res.writeHead(200, { 'Content-Type': 'image/png', 'Content-Length': body.length, 'Cache-Control': 'no-store' }); res.end(body); }, fail);
      if (route === '/status') return enqueue(async () => ({ service, url: page.url(), title: await page.title(), authenticated: await authenticated() })).then(v => json(v), fail);
      return json({ error: 'not_found' }, 404);
    }
    if (req.method !== 'POST') return json({ error: 'method_not_allowed' }, 405);
    let raw = ''; req.on('data', chunk => { if (raw.length < 16384) raw += chunk; }); req.on('end', () => {
      let value; try { value = JSON.parse(raw || '{}'); } catch { return fail(); }
      enqueue(async () => {
        if (route === '/click') await page.mouse.click(Number(value.x), Number(value.y));
        else if (route === '/type') { if (value.clear) { await page.keyboard.press('Control+A'); await page.keyboard.press('Backspace'); } await page.keyboard.insertText(String(value.text || '').slice(0, 4096)); }
        else if (route === '/key') { if (!allowedKeys.has(value.key)) throw new Error('Unsupported key'); await page.keyboard.press(value.key); }
        else if (route === '/navigate') await navigate(value.service);
        else if (route === '/complete') { const ready = await authenticated(); const captured = ready && await captureAuthenticatedSession(); const complete = Boolean(ready && captured); writeProtectedJson(path.join(args.stateDir, 'handoff-ready.json'), { service, authenticated: complete, credentialCaptured: captured, completedAt: new Date().toISOString(), url: page.url() }); return { status: complete ? 'ok' : 'auth_required', service, authenticated: complete, credentialCaptured: captured }; }
        else throw new Error('Not found');
        return { status: 'ok' };
      }).then(v => json(v), fail);
    });
  });
  server.listen(args.port, '127.0.0.1', () => console.log(JSON.stringify({ status: 'ready', port: args.port, service, stateDir: args.stateDir })));
  const stop = async () => { server.close(); await context.close(); process.exit(0); };
  process.on('SIGINT', stop); process.on('SIGTERM', stop);
}

run().catch(error => { console.error(JSON.stringify({ status: 'error', error: error.message })); process.exit(1); });
