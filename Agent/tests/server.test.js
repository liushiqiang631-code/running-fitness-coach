import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import http from 'node:http';

import { createAppServer } from '../server.js';

test('server renders the product shell and health endpoint', async (t) => {
  const server = createAppServer({ ragBaseUrl: 'http://127.0.0.1:1' });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => server.close());
  const { port } = server.address();

  const page = await fetch(`http://127.0.0.1:${port}/`);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /STRIDE/);

  const health = await fetch(`http://127.0.0.1:${port}/api/health`);
  assert.equal(health.status, 200);
  assert.deepEqual(await health.json(), { status: 'ok', app: 'stride-coach' });
});

test('RAG proxy returns actionable unavailable response', async (t) => {
  const server = createAppServer({ ragBaseUrl: 'http://127.0.0.1:1' });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => server.close());
  const { port } = server.address();

  const response = await fetch(`http://127.0.0.1:${port}/api/rag/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: '今天怎么练？', history: [] }),
  });
  assert.equal(response.status, 503);
  assert.match((await response.json()).error, /知识服务暂未连接/);
});

test('RAG proxy preserves the upstream SSE stream', async (t) => {
  const rag = createSseStub();
  rag.listen(0, '127.0.0.1');
  await once(rag, 'listening');
  t.after(() => rag.close());
  const ragPort = rag.address().port;

  const server = createAppServer({ ragBaseUrl: `http://127.0.0.1:${ragPort}` });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => server.close());
  const { port } = server.address();

  const response = await fetch(`http://127.0.0.1:${port}/api/rag/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: '阈值跑怎么练？', history: [] }),
  });

  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type'), /text\/event-stream/);
  assert.equal(await response.text(), upstreamStream);
});

test('RAG proxy keeps a slow first stream chunk alive', async (t) => {
  const rag = http.createServer((request, response) => {
    setTimeout(() => {
      response.writeHead(200, { 'content-type': 'text/event-stream; charset=utf-8' });
      response.end('data: {"delta":"慢首段回答"}\n\ndata: [DONE]\n\n');
    }, 31_000);
  });
  rag.listen(0, '127.0.0.1');
  await once(rag, 'listening');
  t.after(() => rag.close());

  const server = createAppServer({ ragBaseUrl: `http://127.0.0.1:${rag.address().port}` });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => server.close());

  const response = await fetch(`http://127.0.0.1:${server.address().port}/api/rag/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: '慢响应测试', history: [] }),
  });

  assert.equal(response.status, 200);
  assert.equal(await response.text(), 'data: {"delta":"慢首段回答"}\n\ndata: [DONE]\n\n');
});

test('static response sends baseline browser security headers', async (t) => {
  const server = createAppServer({ ragBaseUrl: 'http://127.0.0.1:1' });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => server.close());
  const { port } = server.address();

  const response = await fetch(`http://127.0.0.1:${port}/`);
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(response.headers.get('referrer-policy'), 'no-referrer');
  assert.match(response.headers.get('content-security-policy'), /default-src 'self'/);
});

function createSseStub() {
  return http.createServer(async (request, response) => {
    let body = '';
    for await (const chunk of request) body += chunk;
    assert.deepEqual(JSON.parse(body), { query: '阈值跑怎么练？', history: [] });
    response.writeHead(200, { 'content-type': 'text/event-stream; charset=utf-8' });
    response.end(upstreamStream);
  });
}

const upstreamStream = [
  'data: {"delta":"真实知识回答"}\n\n',
  'data: {"sources":[{"title":"丹尼尔斯经典跑步训练法","page":"91"}]}\n\n',
  'data: [DONE]\n\n',
].join('');
