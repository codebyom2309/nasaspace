/**
 * Mission Architect - Node.js Server & Google AI Studio Runner
 * Serves static assets from web/ and proxies or handles /api/* routes.
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, spawnSync } from 'node:child_process';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = parseInt(process.env.PORT || '8600', 10);
const PY_PORT = PORT === 8600 ? 8601 : PORT + 10;
const WEB_DIR = path.join(__dirname, 'web');
const DATA_DIR = path.join(__dirname, 'data');

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.mjs': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
  '.wav': 'audio/wav',
  '.mp3': 'audio/mpeg'
};

// Check for Python executable
function detectPython() {
  const candidates = [
    process.env.PYTHON_BIN,
    'python3',
    'python',
    process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, 'Programs', 'Python', 'Python311', 'python.exe') : null
  ].filter(Boolean);

  for (const cmd of candidates) {
    try {
      const res = spawnSync(cmd, ['--version'], { timeout: 2000 });
      if (res.status === 0) {
        return cmd;
      }
    } catch (_) {}
  }
  return null;
}

const pythonCmd = detectPython();
let pyProcess = null;
let pyReady = false;

if (pythonCmd) {
  console.log(`[Node Runner] Detected Python: ${pythonCmd}. Spawning Python backend on port ${PY_PORT}...`);
  pyProcess = spawn(pythonCmd, ['server.py'], {
    cwd: __dirname,
    env: { ...process.env, PORT: String(PY_PORT) },
    stdio: ['ignore', 'pipe', 'pipe']
  });

  pyProcess.stdout.on('data', (d) => {
    const msg = d.toString();
    process.stdout.write(`[Python] ${msg}`);
    if (msg.includes('Mission Architect on http')) {
      pyReady = true;
    }
  });

  pyProcess.stderr.on('data', (d) => {
    process.stderr.write(`[Python ERR] ${d}`);
  });

  pyProcess.on('exit', (code) => {
    console.log(`[Node Runner] Python backend exited with code ${code}.`);
    pyReady = false;
  });

  process.on('exit', () => {
    if (pyProcess) {
      try { pyProcess.kill(); } catch (_) {}
    }
  });
} else {
  console.log('[Node Runner] Python runtime not detected. Operating in native Node.js mode.');
}

// Helper to serve static files
function serveStatic(req, res, filePath) {
  fs.stat(filePath, (err, stats) => {
    if (err || !stats.isFile()) {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'File not found' }));
      return;
    }

    const ext = path.extname(filePath).toLowerCase();
    const contentType = MIME_TYPES[ext] || 'application/octet-stream';
    res.writeHead(200, {
      'Content-Type': contentType,
      'Content-Length': stats.size,
      'Cache-Control': filePath.endsWith('.html') ? 'no-cache' : 'public, max-age=3600'
    });
    fs.createReadStream(filePath).pipe(res);
  });
}

// Proxy to Python server
function proxyToPython(req, res) {
  const options = {
    hostname: '127.0.0.1',
    port: PY_PORT,
    path: req.url,
    method: req.method,
    headers: req.headers
  };

  const pReq = http.request(options, (pRes) => {
    res.writeHead(pRes.statusCode, pRes.headers);
    pRes.pipe(res);
  });

  pReq.on('error', () => {
    fallbackApi(req, res);
  });

  req.pipe(pReq);
}

// Native Node.js API fallback handlers
function fallbackApi(req, res) {
  const parsedUrl = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  const pathname = parsedUrl.pathname;

  if (pathname === '/api/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok', runtime: 'node', pyReady }));
    return;
  }

  if (pathname === '/api/catalog') {
    const catalogPath = path.join(DATA_DIR, 'catalog.json');
    if (fs.existsSync(catalogPath)) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      fs.createReadStream(catalogPath).pipe(res);
      return;
    }
  }

  if (pathname === '/api/leaderboard') {
    const lbPath = path.join(DATA_DIR, 'leaderboard.json');
    let rows = [];
    if (fs.existsSync(lbPath)) {
      try {
        rows = JSON.parse(fs.readFileSync(lbPath, 'utf8'));
      } catch (_) {}
    }
    const scenario = parsedUrl.searchParams.get('scenario') || 'wildfire';
    const filtered = rows.filter((r) => r.scenario === scenario).sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 10);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(filtered));
    return;
  }

  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ ok: true, fallback: true }));
}

// Create Main HTTP Server
const server = http.createServer((req, res) => {
  const parsedUrl = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
  let pathname = decodeURIComponent(parsedUrl.pathname);

  // API Requests
  if (pathname.startsWith('/api/')) {
    if (pyProcess && pyReady) {
      proxyToPython(req, res);
    } else {
      fallbackApi(req, res);
    }
    return;
  }

  // Root or Web static files
  if (pathname === '/' || pathname === '/index.html') {
    serveStatic(req, res, path.join(WEB_DIR, 'index.html'));
    return;
  }

  if (pathname.startsWith('/web/')) {
    pathname = pathname.slice(4);
  }

  const safePath = path.normalize(pathname).replace(/^(\.\.[\/\\])+/, '');
  const candidate = path.join(WEB_DIR, safePath);

  if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
    serveStatic(req, res, candidate);
  } else {
    // Fallback to web/index.html for client-side navigation
    serveStatic(req, res, path.join(WEB_DIR, 'index.html'));
  }
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`Mission Architect server listening on http://localhost:${PORT}`);
});

