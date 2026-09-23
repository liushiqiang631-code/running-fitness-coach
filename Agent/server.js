import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.resolve(__dirname, 'public');
const MAX_BODY_BYTES = 1024 * 1024;
const RAG_UNAVAILABLE_MESSAGE =
  '知识服务暂未连接；请启动 rag/start_server.bat 后重试。训练计划与数据功能仍可使用。';
const SECURITY_HEADERS = {
  'content-security-policy': "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
  'referrer-policy': 'no-referrer',
  'x-content-type-options': 'nosniff',
  'x-frame-options': 'DENY',
};

const MIME_TYPES = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.map': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
};

function sendJson(response, status, body) {
  const content = JSON.stringify(body);
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(content),
    'cache-control': 'no-store',
  });
  response.end(content);
}

function sendMethodNotAllowed(response, allowed) {
  response.writeHead(405, { allow: allowed });
  response.end();
}

function sendRagUnavailable(response) {
  sendJson(response, 503, { error: RAG_UNAVAILABLE_MESSAGE });
}

function readRequestBody(request) {
  return new Promise((resolve, reject) => {
    const contentLength = Number(request.headers['content-length']);
    if (Number.isFinite(contentLength) && contentLength > MAX_BODY_BYTES) {
      request.resume();
      reject(Object.assign(new Error('Request body too large'), { statusCode: 413 }));
      return;
    }

    const chunks = [];
    let size = 0;
    let tooLarge = false;

    request.on('data', (chunk) => {
      if (tooLarge) return;
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        tooLarge = true;
        reject(Object.assign(new Error('Request body too large'), { statusCode: 413 }));
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => {
      if (!tooLarge) resolve(Buffer.concat(chunks));
    });
    request.on('error', reject);
  });
}

function proxyRequest({ ragBaseUrl, request, response, targetPath, body }) {
  let target;
  try {
    target = new URL(targetPath, ragBaseUrl);
  } catch {
    sendRagUnavailable(response);
    return;
  }

  if (target.protocol !== 'http:') {
    sendRagUnavailable(response);
    return;
  }

  const headers = {};
  if (body) {
    headers['content-length'] = body.length;
    if (request.headers['content-type']) headers['content-type'] = request.headers['content-type'];
  }
  if (request.headers.accept) headers.accept = request.headers.accept;

  const upstream = http.request(
    target,
    { method: request.method, headers },
    (upstreamResponse) => {
      const responseHeaders = {};
      const contentType = upstreamResponse.headers['content-type'];
      if (contentType) responseHeaders['content-type'] = contentType;
      if (upstreamResponse.headers['cache-control']) {
        responseHeaders['cache-control'] = upstreamResponse.headers['cache-control'];
      }
      response.writeHead(upstreamResponse.statusCode || 502, responseHeaders);
      upstreamResponse.pipe(response);
    },
  );

  upstream.once('error', () => {
    if (!response.headersSent) sendRagUnavailable(response);
    else response.destroy();
  });
  // 首次检索与模型首段响应可能超过 30 秒；流一旦开始会持续透传。
  upstream.setTimeout(90_000, () => upstream.destroy(new Error('RAG request timed out')));
  if (body) upstream.end(body);
  else upstream.end();
}

function staticFilePath(pathname) {
  let decodedPath;
  try {
    decodedPath = decodeURIComponent(pathname);
  } catch {
    return null;
  }

  const relativePath = decodedPath === '/' ? 'index.html' : decodedPath.replace(/^\/+/, '');
  const filePath = path.resolve(PUBLIC_DIR, relativePath.split('/').join(path.sep));
  if (filePath !== PUBLIC_DIR && !filePath.startsWith(`${PUBLIC_DIR}${path.sep}`)) return null;
  return filePath;
}

function serveStatic(request, response, pathname) {
  const requestedPath = staticFilePath(pathname);
  const indexPath = path.join(PUBLIC_DIR, 'index.html');
  const pathToServe = requestedPath && fs.existsSync(requestedPath) && fs.statSync(requestedPath).isFile()
    ? requestedPath
    : indexPath;

  if (!fs.existsSync(pathToServe) || !fs.statSync(pathToServe).isFile()) {
    response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
    response.end('Not found');
    return;
  }

  response.writeHead(200, {
    'content-type': MIME_TYPES[path.extname(pathToServe).toLowerCase()] || 'application/octet-stream',
  });
  if (request.method === 'HEAD') {
    response.end();
    return;
  }
  fs.createReadStream(pathToServe)
    .on('error', () => {
      if (!response.headersSent) response.writeHead(500);
      response.end();
    })
    .pipe(response);
}

export function createAppServer({ ragBaseUrl = process.env.RAG_BASE_URL || 'http://127.0.0.1:8008' } = {}) {
  return http.createServer(async (request, response) => {
    for (const [name, value] of Object.entries(SECURITY_HEADERS)) response.setHeader(name, value);
    const requestUrl = new URL(request.url || '/', 'http://localhost');
    const { pathname } = requestUrl;

    if (pathname === '/api/health') {
      if (request.method !== 'GET') return sendMethodNotAllowed(response, 'GET');
      return sendJson(response, 200, { status: 'ok', app: 'stride-coach' });
    }

    if (pathname === '/api/rag/health') {
      if (request.method !== 'GET') return sendMethodNotAllowed(response, 'GET');
      return proxyRequest({ ragBaseUrl, request, response, targetPath: '/health' });
    }

    if (pathname === '/api/rag/chat') {
      if (request.method !== 'POST') return sendMethodNotAllowed(response, 'POST');
      try {
        const body = await readRequestBody(request);
        return proxyRequest({ ragBaseUrl, request, response, targetPath: '/chat', body });
      } catch (error) {
        return sendJson(response, error.statusCode || 400, {
          error: error.statusCode === 413 ? 'Request body too large' : 'Invalid request body',
        });
      }
    }

    if (pathname.startsWith('/api/')) {
      response.writeHead(404, { 'content-type': 'application/json; charset=utf-8' });
      response.end(JSON.stringify({ error: 'Not found' }));
      return;
    }

    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return sendMethodNotAllowed(response, 'GET, HEAD');
    }
    return serveStatic(request, response, pathname);
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const host = process.env.HOST || '127.0.0.1';
  const port = Number(process.env.PORT || 3000);
  createAppServer().listen(port, host, () => {
    console.log(`STRIDE Coach listening at http://${host}:${port}`);
  });
}
